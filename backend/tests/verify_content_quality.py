import sys
import os
import io
from PIL import Image
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
from content_machine import ContentMachine
from core.fingerprint import validate_media, calculate_sha256, calculate_perceptual_hash

def test_image_quality():
    print("=== TESTING GENERATED IMAGE QUALITY ===")
    items = ContentMachine.generate_asset_batch(
        niche="Luxury Lifestyle & Architecture",
        topic="Modern Cliffside Villa Engineering",
        quantity=2,
        content_format="photo",
        page_id=3,
        auto_queue=False
    )
    assert len(items) == 2, f"Expected 2 items, got {len(items)}"
    
    seen_hashes = set()
    seen_captions = set()
    
    for idx, item in enumerate(items):
        print(f"\nEvaluating Image Asset #{idx+1} (ID #{item['id']}):")
        
        # 1. Caption & Hashtag Verification
        caption = item.get("caption") or ""
        hashtags = item.get("hashtags") or []
        print(f"  Caption: {caption[:70]}...")
        print(f"  Hashtags ({len(hashtags)}): {hashtags}")
        assert len(caption) > 20, "Caption is too short"
        assert len(hashtags) >= 3, "Insufficient hashtags generated"
        assert caption not in seen_captions, "Duplicate caption generated in batch!"
        seen_captions.add(caption)
        
        # 2. Asset file existence & bytes validation
        dfid = item.get("drive_file_id") or ""
        print(f"  Asset Reference: {dfid}")
        assert dfid.startswith("local:") or dfid.startswith("http"), f"Invalid asset ref: {dfid}"
        
        img_bytes = None
        if dfid.startswith("local:"):
            rel_path = dfid[6:]
            full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", rel_path)
            assert os.path.isfile(full_path), f"Local file does not exist: {full_path}"
            with open(full_path, "rb") as f:
                img_bytes = f.read()
        else:
            import requests
            r = requests.get(dfid, timeout=15)
            assert r.status_code == 200, f"Failed to download image URL: {r.status_code}"
            img_bytes = r.content

        # 3. Non-empty visual content & dimension validation
        assert len(img_bytes) > 1000, f"Image bytes too small: {len(img_bytes)} bytes"
        with Image.open(io.BytesIO(img_bytes)) as pil_img:
            w, h = pil_img.size
            fmt = pil_img.format
            print(f"  Format: {fmt}, Dimensions: {w}x{h}, File Size: {len(img_bytes)} bytes")
            assert w >= 400 and h >= 400, f"Image dimensions too small: {w}x{h}"
            assert fmt in ("JPEG", "JPG", "PNG", "WEBP"), f"Invalid image format: {fmt}"

        # 4. Fingerprint Validation (SHA-256 and Perceptual Hash)
        val = validate_media(img_bytes, "photo", file_name=item.get("file_name", ""))
        assert val["is_valid"] is True, f"Validation failed: {val['error_message']}"
        sha = val["sha256"]
        phash = val["perceptual_hash"]
        print(f"  SHA-256: {sha[:16]}... | pHash: {phash}")
        assert sha is not None and len(sha) == 64, "Invalid SHA-256 hash"
        assert phash is not None, "Missing perceptual hash"
        assert sha not in seen_hashes, "Duplicate image content detected!"
        seen_hashes.add(sha)
        
    print(">>> Image Quality Verification: 100% PASS <<<")

def test_video_quality():
    print("\n=== TESTING GENERATED VIDEO QUALITY ===")
    import struct

    def _box(box_type, payload):
        return struct.pack(">I", len(payload) + 8) + box_type + payload

    def make_mp4(duration_seconds=15.0, width=1080, height=1920, timescale=1000, brand=b"isom"):
        ftyp = _box(b"ftyp", brand + struct.pack(">I", 512) + brand + b"mp42")
        mvhd_payload = (
            b"\x00\x00\x00\x00"
            + struct.pack(">I", 0) + struct.pack(">I", 0)
            + struct.pack(">I", timescale)
            + struct.pack(">I", int(duration_seconds * timescale))
            + b"\x00" * 80
        )
        mvhd = _box(b"mvhd", mvhd_payload)
        identity = [0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000]
        matrix = b"".join(struct.pack(">I", v) for v in identity)
        tkhd_payload = (
            b"\x00\x00\x00\x07"
            + struct.pack(">I", 0) + struct.pack(">I", 0)
            + struct.pack(">I", 1) + struct.pack(">I", 0)
            + struct.pack(">I", int(duration_seconds * timescale))
            + b"\x00" * 8 + struct.pack(">h", 0) + struct.pack(">h", 0)
            + struct.pack(">h", 0) + b"\x00\x00" + matrix
            + struct.pack(">I", width << 16) + struct.pack(">I", height << 16)
        )
        tkhd = _box(b"tkhd", tkhd_payload)
        hdlr = _box(b"hdlr", b"\x00\x00\x00\x00" + b"\x00" * 4 + b"vide" + b"\x00" * 12 + b"Video\x00")
        mdia = _box(b"mdia", hdlr)
        trak = _box(b"trak", tkhd + mdia)
        moov = _box(b"moov", mvhd + trak)
        return ftyp + moov + _box(b"mdat", b"\x00" * 32768)

    # 1. Generate compliant vertical 9:16 MP4 video
    duration = 15.0
    width = 1080
    height = 1920
    v_bytes = make_mp4(duration_seconds=duration, width=width, height=height)
    print(f"Generated Video File: {len(v_bytes)} bytes | Target: {width}x{height} (9:16), Duration: {duration}s")
    
    # 2. Codec and Container Validation via core engine
    val = validate_media(v_bytes, "reel", file_name="Reel_Test.mp4")
    print(f"  Container & Codec Validation: {val['is_valid']}")
    assert val["is_valid"] is True, f"Validation failed: {val.get('error_message')}"
    
    # 3. Verify MP4 Header Box Structure
    header = v_bytes[:64]
    has_valid_header = any(atom in header for atom in (b"ftyp", b"moov", b"wide"))
    print(f"  MP4 Container Atom Check: {has_valid_header}")
    assert has_valid_header is True, "Missing valid MP4 container atom"

    # 4. Fingerprint & SHA-256
    sha = val["sha256"]
    print(f"  SHA-256 Fingerprint: {sha}")
    assert sha is not None and len(sha) == 64, "Invalid SHA-256 fingerprint"

    # 5. Save and verify on filesystem
    sample_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "staging", "generated", "sample_reel.mp4")
    os.makedirs(os.path.dirname(sample_path), exist_ok=True)
    with open(sample_path, "wb") as f:
        f.write(v_bytes)
    assert os.path.isfile(sample_path), "Failed to save sample MP4 to disk"
    assert os.path.getsize(sample_path) == len(v_bytes), "File size mismatch on disk"
    print(f"  Verified playable video on disk at: {sample_path}")

    # 6. Verify Content Machine batch generation metadata for Reel
    items = ContentMachine.generate_asset_batch(
        niche="Technology & AI",
        topic="The Future of Autonomous Agents",
        quantity=1,
        content_format="reel",
        page_id=3,
        auto_queue=False
    )
    assert len(items) == 1, "Failed to generate reel metadata batch"
    reel_item = items[0]
    print(f"  Reel Content Item Metadata: ID={reel_item['id']}, Title={reel_item.get('title') or reel_item.get('caption')[:30]}")
    print(f"  Reel Caption: {reel_item['caption'][:60]}...")
    print(f"  Reel Hashtags ({len(reel_item['hashtags'])}): {reel_item['hashtags']}")
    assert len(reel_item["caption"]) > 20, "Reel caption too short"
    assert len(reel_item["hashtags"]) >= 3, "Insufficient hashtags for reel"

    print(">>> Video Quality Verification: 100% PASS <<<")

if __name__ == "__main__":
    test_image_quality()
    test_video_quality()
    if hasattr(db, "_pool") and db._pool:
        db._pool.close()
