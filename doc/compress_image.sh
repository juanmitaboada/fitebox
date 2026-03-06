#!/bin/bash

# 1. Check if an argument (image file) is provided
if [ -z "$1" ]; then
    echo "❌ Error: No image specified."
    echo "Usage: ./compress_image.sh image_name.png"
    exit 1
fi

FILE="$1"

# 2. Check if the file exists
if [ ! -f "$FILE" ]; then
    echo "❌ Error: '$FILE' not found."
    exit 1
fi

echo "🔄 Processing image: $FILE ..."

# Name of temporary files
TEMP_1="temp_step1.jpg"
TEMP_2="temp_step2.png"

# --- START OF FFMPEG PROCESS ---

# Step 1: Convert to JPG with low quality (introduces artifacts)
# -v error: Hide unnecessary ffmpeg logs
ffmpeg -v error -y -i "$FILE" -q:v 5 "$TEMP_1"

# Step 2: Convert to PNG with 8-bit palette (posterization effect)
ffmpeg -v error -y -i "$TEMP_1" -pix_fmt pal8 "$TEMP_2"

# Step 3: Scale to 1920x1080 and overwrite the original
ffmpeg -v error -y -i "$TEMP_2" -vf scale=1920:1080 "$FILE"

# --- END OF FFMPEG PROCESS ---

# Clean up temporary files
rm "$TEMP_1" "$TEMP_2"

echo "✅ Done! The image has been modified and overwritten."
