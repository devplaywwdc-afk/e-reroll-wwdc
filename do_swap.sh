#!/system/bin/sh
set -e
echo "=== 1. Stopping eFootball ==="
am force-stop jp.konami.pesam || true
sleep 1

BASE_PATH=$(pm path jp.konami.pesam | grep base | head -n 1 | cut -d: -f2)
TARGET_DIR=$(dirname "$BASE_PATH")
LIB_DIR="$TARGET_DIR/lib/arm64"

echo "TARGET_DIR=$TARGET_DIR"
echo "LIB_DIR=$LIB_DIR"

if [ -z "$TARGET_DIR" ] || [ ! -d "$TARGET_DIR" ]; then
    echo "ERROR: Target directory not found!"
    exit 1
fi

echo "=== 2. Backing up original base.apk ==="
if [ ! -f /data/local/tmp/pesam_base_orig.apk ]; then
    cp "$TARGET_DIR/base.apk" /data/local/tmp/pesam_base_orig.apk
fi

echo "=== 3. Swapping base.apk ==="
cp /data/local/tmp/pesam_base.final2.apk "$TARGET_DIR/base.apk"
chmod 644 "$TARGET_DIR/base.apk"
chown system:system "$TARGET_DIR/base.apk"

echo "=== 4. Copying Frida Gadget ==="
mkdir -p "$LIB_DIR"
cp /data/local/tmp/libfrida-gadget.so "$LIB_DIR/libfrida-gadget.so"
cp /data/local/tmp/libfrida-gadget.config.so "$LIB_DIR/libfrida-gadget.config.so"
chmod 755 "$LIB_DIR/libfrida-gadget.so"
chmod 644 "$LIB_DIR/libfrida-gadget.config.so"
chown system:system "$LIB_DIR/libfrida-gadget.so" "$LIB_DIR/libfrida-gadget.config.so"

echo "=== 5. Cleaning oat and dalvik cache ==="
rm -rf "$TARGET_DIR/oat" 2>/dev/null || true
rm -rf /data/dalvik-cache/*pesam* 2>/dev/null || true

echo "=== 6. Verification ==="
ls -lh "$TARGET_DIR/base.apk"
ls -lh "$LIB_DIR" | grep -i frida
echo "SWAP_SUCCESS"

