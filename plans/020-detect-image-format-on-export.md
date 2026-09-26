# Plan 020: Downloads and ZIP export use the image's real format

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat bb87099..HEAD -- app.py tests/test_app.py`
> Changes from plans 018/019 are expected. Compare the excerpts below against
> the live code; on a mismatch, STOP.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (plan 021 reuses the helper this plan adds)
- **Category**: bug
- **Planned at**: commit `bb87099`, 2026-09-26

## Why this matters

Stored results are PNG (OpenAI) or whatever MIME type Gemini returns, which can
be JPEG. The ZIP export always names files `img_NNNN.jpg`, even when they
contain PNG bytes. `/download` always sends `image/png` with a `.png` name.
Some viewers and uploaders reject files whose extension doesn't match their
content. A small helper that sniffs the bytes fixes both, and plan 021 will
reuse it when it writes images to disk.

## Current state

- `/export-zip`, `app.py:998-1031`:
  ```python
  if row["result_b64"]:
      img_data = base64.b64decode(row["result_b64"])
      zf.writestr(f"img_{row['id']:04d}.jpg", img_data)
  manifest.append({"id": row["id"], "filename": f"img_{row['id']:04d}.jpg", ...})
  ```
- `/download/<pid>`, `app.py:1467-1486`:
  ```python
  img_data = base64.b64decode(row["result_b64"])
  return send_file(io.BytesIO(img_data), mimetype="image/png",
                   as_attachment=True, download_name=f"img_{pid}.png")
  ```
- `_save_img_to_pictures` (`app.py:1496-1503`) also hard-codes `.png`.
- Pillow is already imported as `from PIL import Image`.

## Scope
**In scope**: `app.py`, `tests/test_app.py`.
**Out of scope**: how images are stored (plan 021), and the FB upload path.

## Steps

### Step 1: Helper
Near `make_thumbnail` in `app.py`, add:
```python
_FORMAT_EXT = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
               "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}

def detect_image_format(img_bytes: bytes) -> tuple:
    """Return (extension, mimetype) by sniffing the bytes; default PNG."""
    try:
        fmt = Image.open(io.BytesIO(img_bytes)).format
    except Exception:
        fmt = None
    return _FORMAT_EXT.get(fmt, ("png", "image/png"))
```
**Verify**: `venv/bin/python -c "import app; print(app.detect_image_format(b'junk'))"` → `('png', 'image/png')`.

### Step 2: Use it
- `/export-zip`: compute `ext, _ = detect_image_format(img_data)`, and use
  `f"img_{row['id']:04d}.{ext}"` for **both** the zip entry and the manifest
  `filename`. For rows with no image, keep the manifest filename as `None`.
- `/download`: `ext, mime = detect_image_format(img_data)`, then pass `mimetype=mime` and `download_name=f"img_{pid}.{ext}"`.
- `_save_img_to_pictures`: derive the extension the same way instead of `.png`.

**Verify**: `grep -n '\.jpg"\|image/png"' app.py` → no hard-coded format remains in these three places.

### Step 3: Tests
Generate real bytes with Pillow (`Image.new("RGB",(4,4)).save(buf, "JPEG")`) and
insert them with `app_module.save_prompt(...)` (see its signature at `app.py:385`).
- `test_download_jpeg_has_jpeg_mimetype` → `Content-Type` is `image/jpeg`, and the filename ends with `.jpg`.
- `test_export_zip_uses_real_extension`: one PNG row and one JPEG row → `zipfile.ZipFile(io.BytesIO(rv.data)).namelist()` contains `img_0001.png` and `img_0002.jpg`, and the manifest filenames match.

**Verify**: `venv/bin/python -m pytest tests/ -q` → all pass.

## Done criteria
- [ ] Tests exit 0 with 2+ new tests
- [ ] Only `app.py`, `tests/test_app.py`, and `plans/README.md` are modified

## STOP conditions
- An existing test asserts on the `.jpg` zip names or on `image/png` for downloads (report it; don't silently rewrite the test).

## Maintenance notes
- Plan 021 must call `detect_image_format` when it picks the on-disk extension.
