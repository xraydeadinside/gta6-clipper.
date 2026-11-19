"""One 'tick' of the factory. GitHub Actions runs it every ~10 minutes.

  1. read Telegram buttons/replies (approve, titles, Flow prompts, /clip, /vod)
  2. publish approved shorts to YouTube
  3. re-render shorts that got a Flow intro
  4. find new moments (Twitch clips of whitelisted streamers / queued links), judge, edit, send for approval
"""
import html
import re
import sys
import time
import traceback
from pathlib import Path

from . import artifacts, brain, edit, state as st, telegram as tg
from .config import OUT, WORK, env, load_config
from .download import download, probe_duration
from .transcribe import transcribe

DEADLINE = time.time() + 40 * 60  # leave room for artifact upload + state commit


def chat_ids() -> tuple[str, str]:
    main = env("TELEGRAM_CHAT_ID", required=True)
    return main, env("TELEGRAM_FLOW_CHAT_ID") or main


def esc(s: str) -> str:
    return html.escape(str(s or ""))


# ------------------------------------------------------------------ texts & buttons
def chosen_title(it: dict) -> str:
    return it.get("custom_title") or it["titles"][min(it.get("title_idx", 0), len(it["titles"]) - 1)]


def full_description(it: dict, cfg: dict) -> str:
    tags = ["shorts", "gta6"] + [h for h in it.get("hashtags", []) if h not in ("shorts", "gta6")]
    desc = it.get("custom_description") or it.get("description", "")
    credit = f"twitch.tv/{it['streamer_login']}"
    if credit not in desc:
        desc += f"\n\n🎥 Streamer: {credit}"
    return desc + "\n\n" + " ".join(f"#{t}" for t in tags[:10])


def review_caption(it: dict) -> str:
    nums = ["1️⃣", "2️⃣", "3️⃣"]
    titles = "\n".join(f"{nums[i]} {esc(t)}" + (" ✅" if not it.get("custom_title") and i == it.get("title_idx", 0) else "")
                       for i, t in enumerate(it["titles"]))
    if it.get("custom_title"):
        titles += f"\n✏️ {esc(it['custom_title'])} ✅"
    intro = "\n🎬 С Flow-интро" if it.get("intro_file_id") else ""
    return (f"🔥 <b>{it['score']:.1f}/10</b> · {esc(it['category'])} · {esc(it['streamer'])} "
            f"({it.get('views', 0)} просм.)\n💬 {esc(it.get('reason_ru'))}\n"
            f"🪝 Хук: <b>{esc(it.get('hook_text'))}</b>{intro}\n\n<b>Названия:</b>\n{titles}\n\n"
            f"<i>{esc((it.get('custom_description') or it.get('description', ''))[:300])}</i>\n#id{it['id']}")


def review_kb(iid: str) -> dict:
    return tg.kb([
        [("1", f"t:{iid}:0"), ("2", f"t:{iid}:1"), ("3", f"t:{iid}:2")],
        [("🔄 Новые названия", f"nt:{iid}"), ("✏️ Своё", f"et:{iid}"), ("📝 Описание", f"ed:{iid}")],
        [("🎬 Перемонтировать", f"re:{iid}"), ("🎨 Промпт для Flow", f"fl:{iid}")],
        [("✅ Опубликовать", f"ok:{iid}"), ("🗑 Отклонить", f"no:{iid}")],
    ])


def flow_kb(iid: str) -> dict:
    return tg.kb([[("👍 Промпт ок", f"fa:{iid}"), ("🔄 Другой", f"fn:{iid}"), ("✏️ Пожелание", f"fw:{iid}")]])


def refresh_review(it: dict) -> None:
    main, _ = chat_ids()
    if it.get("tg_msg"):
        tg.edit_caption(main, it["tg_msg"], review_caption(it), review_kb(it["id"]))


# ------------------------------------------------------------------ telegram handling
def handle_updates(state: dict, cfg: dict) -> None:
    main, flow = chat_ids()
    allowed = {str(main), str(flow)}
    for up in tg.updates(state["tg_offset"]):
        state["tg_offset"] = up["update_id"] + 1
        try:
            if "callback_query" in up:
                cq = up["callback_query"]
                if str(cq["message"]["chat"]["id"]) in allowed:
                    on_button(state, cfg, cq)
            elif "message" in up:
                msg = up["message"]
                if str(msg["chat"]["id"]) in allowed:
                    on_message(state, cfg, msg)
                elif (msg.get("text") or "").startswith(("/start", "/id")):
                    # helps setup: tells a new chat/group its id, does nothing else
                    tg.send(msg["chat"]["id"], f"chat id: <code>{msg['chat']['id']}</code>")
        except Exception as e:
            traceback.print_exc()
            tg.send(main, f"⚠️ Ошибка обработки: {esc(e)}")


def on_button(state: dict, cfg: dict, cq: dict) -> None:
    main, flow = chat_ids()
    action, iid, *rest = cq["data"].split(":")
    it = state["items"].get(iid)
    if not it:
        return tg.answer(cq["id"], "Клип не найден (устарел)")
    it["updated"] = time.time()
    if action == "t":
        it["title_idx"], it["custom_title"] = int(rest[0]), None
        tg.answer(cq["id"], "Название выбрано")
        refresh_review(it)
    elif action == "nt":
        tg.answer(cq["id"], "Генерирую…")
        it["titles"], it["title_idx"], it["custom_title"] = brain.retitle(cfg, it) or it["titles"], 0, None
        refresh_review(it)
    elif action in ("et", "ed", "fw", "re"):
        field = {"et": "title", "ed": "description", "fw": "flow_wish", "re": "edit_wish"}[action]
        ask = {"title": "Ответь на это сообщение своим названием",
               "description": "Ответь на это сообщение своим описанием",
               "flow_wish": "Что поменять в промпте? (можно по-русски)",
               "edit_wish": "Что поменять в монтаже? Например: «больше лица стримера», «меньше зума», "
                            "«начни сразу с аварии», «убери конец», «быстрее склейки»"}[field]
        chat = flow if action == "fw" else main
        m = tg.send(chat, f"{ask} · #id{iid}", force_reply=True)
        state["awaiting"][str(m["message_id"])] = {"id": iid, "field": field}
        tg.answer(cq["id"])
    elif action == "fl":
        send_flow_prompt(state, it)
        tg.answer(cq["id"], "Промпт отправлен в Flow-чат")
    elif action == "fn":
        tg.answer(cq["id"], "Генерирую…")
        it["flow_prompt"] = brain.new_flow_prompt(cfg, it) or it["flow_prompt"]
        send_flow_prompt(state, it)
    elif action == "fa":
        it["flow_approved"] = True
        tg.answer(cq["id"], "Промпт утверждён")
        tg.send(flow, f"👍 Промпт утверждён · #id{iid}\nСгенерируй в Flow (9:16, 3–4 сек) и <b>ответь видео</b> "
                      f"на сообщение с промптом — вставлю как интро.")
    elif action == "ok":
        it["status"] = "publish"
        tg.answer(cq["id"], "В очереди на публикацию")
        tg.edit_caption(main, it["tg_msg"], review_caption(it) + "\n\n⏳ Публикую…")
    elif action == "no":
        it["status"] = "rejected"
        tg.answer(cq["id"], "Отклонено")
        tg.edit_caption(main, it["tg_msg"], review_caption(it) + "\n\n🗑 Отклонено")
    elif action == "done":
        it["status"] = "published"
        tg.answer(cq["id"], "Отмечено как выложено")


def send_flow_prompt(state: dict, it: dict) -> None:
    _, flow = chat_ids()
    m = tg.send(flow, f"🎨 <b>Flow-промпт</b> · {esc(it['streamer'])} · #id{it['id']}\n\n"
                      f"<code>{esc(it.get('flow_prompt'))}</code>\n\n"
                      f"Нажми на промпт, чтобы скопировать. Готовое видео пришли <b>ответом</b> на это сообщение.",
                flow_kb(it["id"]))
    state["awaiting"][str(m["message_id"])] = {"id": it["id"], "field": "intro"}


def on_message(state: dict, cfg: dict, msg: dict) -> None:
    main, _ = chat_ids()
    text = (msg.get("text") or "").strip()
    reply = msg.get("reply_to_message")
    if reply and str(reply["message_id"]) in state["awaiting"]:
        wait = state["awaiting"][str(reply["message_id"])]
        it = state["items"].get(wait["id"])
        if not it:
            return
        it["updated"] = time.time()
        media = msg.get("video") or msg.get("document") or msg.get("animation")
        if wait["field"] == "intro" and media:
            if media.get("file_size", 0) > 20 * 1024 * 1024:
                tg.send(main, "⚠️ Видео больше 20 МБ — бот такое не скачает. Сожми или укороти до 3–4 сек.")
                return
            it["intro_file_id"] = media["file_id"]
            it["status"] = "rerender"
            tg.send(main, f"🎬 Интро получено, перемонтирую #id{it['id']} в следующем запуске.")
        elif wait["field"] == "title" and text:
            it["custom_title"] = text[:100]
            del state["awaiting"][str(reply["message_id"])]
            refresh_review(it)
        elif wait["field"] == "description" and text:
            it["custom_description"] = text[:4000]
            del state["awaiting"][str(reply["message_id"])]
            refresh_review(it)
        elif wait["field"] == "edit_wish" and text:
            it["edit_wish"] = text[:500]
            it["status"] = "replan"
            del state["awaiting"][str(reply["message_id"])]
            tg.send(main, f"🎬 Понял: «{esc(text[:200])}». Перемонтирую #id{it['id']} в следующем запуске.")
        elif wait["field"] == "flow_wish" and text:
            it["flow_prompt"] = brain.new_flow_prompt(cfg, it, wish=text) or it["flow_prompt"]
            del state["awaiting"][str(reply["message_id"])]
            send_flow_prompt(state, it)
        return

    if text.startswith("/clip"):
        urls = re.findall(r"https?://\S+", text)
        for u in urls:
            key = f"url:{u}"
            iid = st.short_id(key)
            state["items"][iid] = {"id": iid, "key": key, "kind": "url", "url": u, "status": "queued",
                                   "streamer": "", "streamer_login": "", "title": "", "views": 0,
                                   "created": time.time(), "updated": time.time()}
        tg.send(main, f"📥 В очереди: {len(urls)}. Обработаю в ближайшем запуске." if urls else
                "Формат: /clip ссылка (клип Twitch / VOD с ?t= / YouTube)")
    elif text.startswith("/vod"):
        urls = re.findall(r"https?://\S+", text)
        for u in urls:
            queue_vod(state, cfg, u)
    elif text.startswith("/status"):
        from collections import Counter
        c = Counter(i.get("status") for i in state["items"].values())
        tg.send(main, "📊 " + (", ".join(f"{k}: {v}" for k, v in c.items()) or "пусто"))
    elif text.startswith("/start") or text.startswith("/help"):
        tg.send(main, "Я монтирую шортсы по GTA 6.\n/clip ссылка — смонтировать конкретный момент\n"
                      "/vod ссылка — найти лучшие моменты в записи стрима по чату\n/status — что в очереди\n"
                      f"Твой chat id: <code>{msg['chat']['id']}</code>")


def queue_vod(state: dict, cfg: dict, url: str) -> None:
    from .vod_chat import fetch_chat, find_spikes
    main, _ = chat_ids()
    tg.send(main, "🔎 Качаю чат стрима, ищу всплески…")
    spikes = find_spikes(fetch_chat(url), top=cfg["discovery"].get("vod_top", 6))
    for sp in spikes:
        key = f"vod:{url}:{int(sp['peak'])}"
        iid = st.short_id(key)
        state["items"][iid] = {"id": iid, "key": key, "kind": "vod", "url": url, "start": sp["start"],
                               "end": sp["end"], "status": "queued", "streamer": "", "streamer_login": "",
                               "title": f"chat spike x{sp['score']} ({sp['kind']})", "views": 0,
                               "created": time.time(), "updated": time.time()}
    tg.send(main, f"📥 Нашёл {len(spikes)} всплесков чата, ставлю в очередь.")


# ------------------------------------------------------------------ processing
def fetch_source(it: dict) -> Path:
    src_path = WORK / it["id"] / "src.mp4"
    if src_path.exists():
        return src_path
    if it["kind"] == "vod":
        path, meta = download(it["url"], src_path, it["start"], it["end"])
    else:
        path, meta = download(it["url"], src_path)
    if not it.get("streamer"):
        it["streamer"] = meta.get("uploader") or meta.get("creator") or "streamer"
        it["streamer_login"] = (meta.get("uploader_id") or it["streamer"]).lower()
    if not it.get("title"):
        it["title"] = meta.get("title") or ""
    it["views"] = it.get("views") or meta.get("view_count") or 0
    return path


def facecam_for(cfg: dict, it: dict):
    """Config wins (exact coords); otherwise the webcam box Gemini found in the video."""
    for s in cfg.get("streamers", []):
        if s.get("login", "").lower() == it.get("streamer_login", "").lower():
            if s.get("facecam") == "none":
                return None
            if s.get("facecam"):
                return s["facecam"]
    return it.get("facecam_box") if cfg["edit"].get("auto_facecam", True) else None


def render_item(cfg: dict, it: dict, src: Path) -> Path:
    e = cfg["edit"]
    sil = edit.detect_silences(src, e["silence_db"], e["silence_min"])
    cam = facecam_for(cfg, it)
    segs = []
    if it.get("edit_plan"):  # the AI editor's own beat-by-beat plan
        try:
            segs = edit.plan_from_beats(it["edit_plan"], sil, it["words"], e, has_cam=bool(cam))
        except Exception as ex:
            print(f"[edit] plan failed, using auto rules: {ex}")
    if not segs:  # fallback: automatic rules
        segs = edit.plan_segments(it["trim_start"], it["trim_end"], sil, it["words"], it["emphasis"], e,
                                  reactions=it.get("reactions", []), focus=it.get("focus", []), has_cam=bool(cam))
    it["has_cam"] = bool(cam)
    words_out = edit.map_words(it["words"], segs, it["emphasis"])
    callouts = []
    for c in it.get("callouts", []):
        t = edit.map_time(c["t"], segs)
        if t is not None:
            callouts.append({"t": t, "text": c["text"]})
    out = OUT / f"{it['id']}.mp4"
    tmp = WORK / it["id"] / "main.mp4"
    edit.render(src, tmp, segs, words_out, e, hook=it.get("hook_text", ""),
                credit=f"twitch.tv/{it['streamer_login']}", facecam=cam, callouts=callouts,
                work=WORK / it["id"])
    if it.get("intro_file_id"):
        intro = tg.download_file(it["intro_file_id"], WORK / it["id"] / "intro.mp4")
        edit.prepend_intro(intro, tmp, out)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(out)
    it["artifact"] = artifacts.current_name()
    it["file"] = out.name
    return out


def send_for_review(it: dict, out: Path) -> None:
    main, _ = chat_ids()
    prev = edit.preview(out, WORK / it["id"] / "preview.mp4")
    m = tg.send_video(main, prev, review_caption(it), review_kb(it["id"]))
    it["tg_msg"] = m["message_id"]
    it["status"] = "review"
    if it.get("edit_story_ru") or it.get("edit_plan"):
        tg.send(main, edit_reasoning(it), reply_to=m["message_id"])


ROLE_RU = {"hook": "хук", "setup": "завязка", "build": "нагнетание", "punchline": "панчлайн",
           "reaction": "реакция", "payoff": "развязка", "filler": "вырезано"}
CAM_RU = {"push": "резкий наезд", "pan": "камера едет к экшену", "whip": "очень быстрый перелёт",
          "hold": "медленный дрейф"}


def edit_reasoning(it: dict) -> str:
    def mmss(t):
        t -= it.get("trim_start", 0)
        return f"{int(max(0, t) // 60)}:{int(max(0, t) % 60):02d}"
    lines = [f"🎬 <b>Как я смонтировал</b> #id{it['id']}", esc(it.get("edit_story_ru", ""))]
    for b in it.get("edit_plan", [])[:14]:
        if b["role"] == "filler":
            what = "✂️ вырезано"
        elif b["shot"] == "cam" and it.get("has_cam"):
            what = "лицо стримера крупно"
        else:
            what = f"игра, {CAM_RU.get(b['camera'], b['camera'])}, зум ×{b['zoom']:.1f}"
        lines.append(f"• {mmss(b['start'])}–{mmss(b['end'])} <b>{ROLE_RU.get(b['role'], b['role'])}</b>: "
                     f"{what}" + (f" — <i>{esc(b['why_ru'])}</i>" if b.get("why_ru") else ""))
    lines.append("\nНе нравится? Нажми «🎬 Перемонтировать» и напиши, что поменять.")
    return "\n".join(lines)


def process_new(state: dict, cfg: dict, it: dict) -> None:
    main, _ = chat_ids()
    src = fetch_source(it)
    dur = probe_duration(src)
    w = cfg["whisper"]
    it["words"] = transcribe(src, w["model"], w["compute_type"])
    verdict = brain.judge(cfg, it, it["words"], src, dur)
    it.update(verdict)
    it["title_idx"] = 0
    if it["score"] < cfg["brain"]["min_score"] and it["kind"] == "clip":
        it["status"] = "skipped"
        print(f"[skip] {it['id']} score {it['score']}: {it['reason_ru']}")
        return
    out = render_item(cfg, it, src)
    send_for_review(it, out)


def publish(state: dict, cfg: dict, it: dict) -> None:
    from . import youtube
    main, _ = chat_ids()
    path = artifacts.fetch(it["artifact"], it["file"], WORK / it["id"] / "final")
    title, desc = chosen_title(it), full_description(it, cfg)
    tags = cfg["youtube"]["default_tags"] + it.get("hashtags", [])
    if cfg["youtube"]["mode"] == "api":
        res = youtube.upload(path, title, desc, tags, cfg, synthetic=bool(it.get("intro_file_id")))
        it["youtube"] = res
        if not res["locked"]:
            it["status"] = "published"
            tg.edit_caption(main, it["tg_msg"], review_caption(it) + f"\n\n✅ Выложено: {res['url']}")
            return
        tg.send(main, "⚠️ YouTube заблокировал видео как приватное: Google-проект ещё не прошёл аудит API. "
                      "Пока выкладывай вручную (файл ниже) и подай заявку на аудит — см. README.")
    tg.send_document(main, path, f"<b>{esc(title)}</b>\n\n{esc(desc)}")
    tg.send(main, f"Выложи файл выше в YouTube (название и описание — в подписи). #id{it['id']}",
                tg.kb([[("✅ Я выложил", f"done:{it['id']}")]]))
    it["status"] = "manual"


def replan(state: dict, cfg: dict, it: dict) -> None:
    src = fetch_source(it)
    dur = probe_duration(src)
    it.update(brain.replan(cfg, it, it.pop("edit_wish", ""), dur, bool(facecam_for(cfg, it))))
    out = render_item(cfg, it, src)
    send_for_review(it, out)


def rerender(state: dict, cfg: dict, it: dict) -> None:
    src = fetch_source(it)
    out = render_item(cfg, it, src)
    send_for_review(it, out)


def discover(state: dict, cfg: dict) -> None:
    from . import twitch
    every = cfg["discovery"]["every_minutes"] * 60
    if time.time() - state.get("last_discovery", 0) < every - 60:
        return
    state["last_discovery"] = time.time()
    for c in twitch.recent_clips(cfg):
        if c["key"] in state["seen"]:
            continue
        iid = st.short_id(c["key"])
        state["seen"][c["key"]] = time.time()
        state["items"][iid] = {**c, "id": iid, "status": "queued", "created": time.time(), "updated": time.time()}


def tick() -> None:
    cfg = load_config()
    state = st.load()
    main, _ = chat_ids()
    try:
        handle_updates(state, cfg)
        for status, fn in (("publish", publish), ("replan", replan), ("rerender", rerender)):
            for it in [i for i in state["items"].values() if i.get("status") == status]:
                try:
                    fn(state, cfg, it)
                except Exception as e:
                    traceback.print_exc()
                    it["fails"] = it.get("fails", 0) + 1
                    if it["fails"] >= 3:
                        it["status"] = "failed"
                    tg.send(main, f"⚠️ {status} #id{it['id']} (попытка {it['fails']}/3): {esc(e)}")
                st.save(state)
        try:
            discover(state, cfg)
        except Exception as e:
            traceback.print_exc()
            print(f"[discover] {e}")
        queued = sorted((i for i in state["items"].values() if i.get("status") == "queued"),
                        key=lambda i: (i["kind"] == "clip", -i.get("views", 0)))
        for it in queued[: cfg["discovery"]["max_new_per_run"]]:
            if time.time() > DEADLINE:
                break
            try:
                process_new(state, cfg, it)
            except Exception as e:
                traceback.print_exc()
                it["fails"] = it.get("fails", 0) + 1
                if it["fails"] >= 2:
                    it["status"] = "failed"
                    tg.send(main, f"⚠️ Не смог обработать {esc(it.get('url'))}: {esc(e)}")
            it["updated"] = time.time()
            st.save(state)
    finally:
        st.save(state)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd == "tick":
        tick()
