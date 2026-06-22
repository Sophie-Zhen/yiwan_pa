"""Guard test for bot.handle_photo album (media-group) aggregation.

Pins the multi-image fix: a Telegram album arrives as N separate photo updates
sharing one media_group_id, and the bot must buffer them and fire ONE vision
call with all images — not N unrelated single-image calls. A lone photo
(media_group_id=None) must still process immediately.

Stubs _process_images (so no real history / LLM / Telegram I/O) and feeds fake
updates. Run: conda run -n assistant python scripts/test_photo_album.py
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import bot


class _Obj:
    """Generic attribute holder for faking Telegram objects."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeFile:
    def __init__(self, data: bytes):
        self._data = data

    async def download_as_bytearray(self):
        return bytearray(self._data)


class _FakeBot:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    async def get_file(self, file_id):
        return _FakeFile(self.files[file_id])


def _make_update(chat_id, file_id, caption=None, media_group_id=None):
    photo = _Obj(file_id=file_id)
    message = _Obj(photo=[photo], caption=caption, media_group_id=media_group_id)
    return _Obj(effective_chat=_Obj(id=chat_id), message=message)


async def main():
    passed = True

    def expect(desc, cond):
        nonlocal passed
        print(f"  {'ok ' if cond else 'FAIL'} {desc}")
        passed &= bool(cond)

    CHAT = 12345
    bot.USER_CHAT_ID = CHAT          # pass _is_authorized
    bot._GROUP_DEBOUNCE_SECONDS = 0.05  # keep the test fast

    calls = []

    async def _fake_process(message, chat_id, images, caption):
        calls.append(
            {"message": message, "chat_id": chat_id,
             "images": list(images), "caption": caption}
        )

    bot._process_images = _fake_process

    fbot = _FakeBot()
    ctx = _Obj(bot=fbot)

    # --- lone photo: processed immediately, one image ---
    fbot.files["s1"] = b"AAA"
    await bot.handle_photo(_make_update(CHAT, "s1", caption="single"), ctx)
    expect("single: one immediate call", len(calls) == 1)
    expect("single: exactly 1 image", calls[0]["images"] == [b"AAA"])
    expect("single: caption passed through", calls[0]["caption"] == "single")

    calls.clear()

    # --- album of 3 sharing media_group_id "G", caption on the 2nd member ---
    members = [("g1", b"X", None), ("g2", b"Y", "订单"), ("g3", b"Z", None)]
    first_msg = None
    for fid, data, cap in members:
        fbot.files[fid] = data
        u = _make_update(CHAT, fid, caption=cap, media_group_id="G")
        if first_msg is None:
            first_msg = u.message
        await bot.handle_photo(u, ctx)

    expect("album: nothing fires before debounce", len(calls) == 0)
    await asyncio.sleep(bot._GROUP_DEBOUNCE_SECONDS + 0.1)
    expect("album: exactly one call after debounce", len(calls) == 1)
    expect("album: all 3 images, in order", calls[0]["images"] == [b"X", b"Y", b"Z"])
    expect("album: caption captured from any member", calls[0]["caption"] == "订单")
    expect("album: reply anchor is first photo", calls[0]["message"] is first_msg)
    expect("album: buffer cleared after flush", "G" not in bot._media_groups)

    # --- unauthorized chat: nothing processed ---
    calls.clear()
    fbot.files["x1"] = b"NOPE"
    await bot.handle_photo(_make_update(99999, "x1"), ctx)
    expect("unauthorized: no processing", len(calls) == 0)

    print("PASS" if passed else "SOME TESTS FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
