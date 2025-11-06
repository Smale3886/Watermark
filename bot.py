import os
import subprocess
import time
import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)

# ---=== CONFIGURATION ===---
# !! REPLACE WITH YOUR OWN VALUES !!
API_ID = 28103139  # Your API_ID from my.telegram.org
API_HASH = "5a690e3f95c47aeafa44e721558470f1"  # Your API_HASH
BOT_TOKEN = "8260879790:AAFtE1lcZQJQActWAnwTFBabLqlGnK0vewE"  # Your Bot Token

app = Client(
    "watermark_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# This dictionary will hold temporary data for each user
# e.g., user_data[user_id] = {"file_id": "...", "message_id": 123, "resolution": "720p"}
user_data = {}


# ---=== KEYBOARDS ===---

def get_resolution_keyboard():
    """Returns the keyboard for selecting resolution."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1080p", callback_data="res_1080"),
                InlineKeyboardButton("720p", callback_data="res_720"),
                InlineKeyboardButton("480p", callback_data="res_480"),
            ],
            [
                InlineKeyboardButton("Original Quality", callback_data="res_original"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_process")
            ]
        ]
    )

def get_position_keyboard():
    """Returns the keyboard for selecting watermark position."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Top Left", callback_data="pos_tl"),
                InlineKeyboardButton("Top Right", callback_data="pos_tr"),
            ],
            [
                InlineKeyboardButton("Bottom Left", callback_data="pos_bl"),
                InlineKeyboardButton("Bottom Right", callback_data="pos_br"),
            ],
            [
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_process")
            ]
        ]
    )

# ---=== VIDEO HANDLER (Step 1) ===---

@app.on_message(filters.video & filters.private)
async def handle_video(client, message: Message):
    """Handles incoming video files, starts the conversation."""
    user_id = message.from_user.id
    
    # Check for the watermark file
    if not os.path.exists("logo.png"):
        await message.reply_text(
            "⚠️ **Error:** `logo.png` not found.\n\n"
            "Please place your watermark file named `logo.png` in the same "
            "directory as the bot script."
        )
        return

    # Check if user is already processing a file
    if user_id in user_data:
        await message.reply_text(
            "You are already processing another video. "
            "Please wait for it to finish or /cancel."
        )
        return

    # Store file_id and message_id to use later
    user_data[user_id] = {
        "file_id": message.video.file_id,
        "original_message_id": message.id
    }
    
    # File size warning
    if message.video.file_size > 2147483648: # 2GB
        await message.reply_text(
            "⚠️ **Warning:** This video is over 2GB. The bot will try to process it, "
            "but may fail if the final watermarked file is also over 2GB "
            "(or 4GB for Premium users)."
        )

    # Ask the first question
    await message.reply_text(
        "Video received. Please select a resolution:",
        reply_markup=get_resolution_keyboard(),
        quote=True
    )


# ---=== CALLBACK HANDLER (Steps 2, 3, etc.) ===---

@app.on_callback_query()
async def handle_callback_query(client, callback: CallbackQuery):
    """Handles all button presses."""
    user_id = callback.from_user.id
    data = callback.data
    
    # Check if we have data for this user
    if user_id not in user_data:
        await callback.answer("This process seems to have expired.", show_alert=True)
        await callback.message.edit_text("Error: Process data not found. Please send the video again.")
        return

    # --- Cancel Button ---
    if data == "cancel_process":
        # Remove user data
        del user_data[user_id]
        await callback.answer("Canceled", show_alert=False)
        await callback.message.edit_text("✅ Process Canceled.")
        return

    # --- Step 2: Resolution Selected ---
    if data.startswith("res_"):
        resolution = data.split("_")[1]
        user_data[user_id]["resolution"] = resolution
        
        await callback.answer(f"Selected: {resolution}")
        await callback.message.edit_text(
            f"Resolution set to **{resolution}**.\n\n"
            "Now, please select the watermark position:",
            reply_markup=get_position_keyboard()
        )
        return

    # --- Step 3: Position Selected (Start Processing) ---
    if data.startswith("pos_"):
        position = data.split("_")[1]
        user_data[user_id]["position"] = position
        
        await callback.answer(f"Position set: {position}")
        await callback.message.edit_text("Got it! Starting process...")
        
        # Start the long process
        await process_video(client, callback.message, user_id)
        return


# ---=== VIDEO PROCESSING (Final Step) ===---

async def process_video(client, status_msg: Message, user_id):
    """Downloads, watermarks, and uploads the video."""
    
    # Get the original message to reply to
    original_message_id = user_data[user_id]["original_message_id"]
    
    # Define file paths
    input_video_path = f"input_{user_id}.mp4"
    output_video_path = f"output_{user_id}.mp4"

    try:
        # 1. Download the video
        await status_msg.edit_text("Downloading video...")
        start_time = time.time()
        
        input_video_path = await client.download_media(
            message=user_data[user_id]["file_id"],
            file_name=input_video_path,
            progress=progress_bar,
            progress_args=(status_msg, start_time, "Downloading")
        )

        # 2. Build FFmpeg command
        await status_msg.edit_text("Building FFmpeg command...")
        ffmpeg_cmd = build_ffmpeg_cmd(user_id, input_video_path, output_video_path)
        
        if not ffmpeg_cmd:
            await status_msg.edit_text("❌ Error: Could not build FFmpeg command.")
            return

        # 3. Run the FFmpeg command
        await status_msg.edit_text("Applying watermark... (This may take a while)")
        
        cmd_success = await run_ffmpeg_command(ffmpeg_cmd)
        
        if not cmd_success or not os.path.exists(output_video_path):
            await status_msg.edit_text("❌ **Error:** Failed to apply watermark.")
            cleanup_files([input_video_path]) # Clean up input
            return

        # 4. Upload the watermarked video
        await status_msg.edit_text("Uploading watermarked video...")
        start_time = time.time()
        
        # Reply to the *original* video message
        await client.send_video(
            chat_id=user_id,
            video=output_video_path,
            caption="Here is your watermarked video! ✨",
            reply_to_message_id=original_message_id,
            progress=progress_bar,
            progress_args=(status_msg, start_time, "Uploading")
        )
        
        # 5. Clean up
        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"An error occurred: {e}")
        print(f"Error: {e}")
    
    finally:
        # Always clean up files and user data
        cleanup_files([input_video_path, output_video_path])
        if user_id in user_data:
            del user_data[user_id]


def build_ffmpeg_cmd(user_id, input_path, output_path):
    """Builds the dynamic FFmpeg command based on user choices."""
    
    data = user_data.get(user_id)
    if not data:
        return None

    resolution = data.get("resolution")
    position = data.get("position")

    # --- Overlay Position Logic ---
    if position == "tl":   # Top Left
        overlay_cmd = "overlay=10:10"
    elif position == "tr": # Top Right
        overlay_cmd = "overlay=W-w-10:10"
    elif position == "bl": # Bottom Left
        overlay_cmd = "overlay=10:H-h-10"
    elif position == "br": # Bottom Right
        overlay_cmd = "overlay=W-w-10:H-h-10"
    else:
        overlay_cmd = "overlay=W-w-10:10" # Default to Top Right

    # --- Resolution Logic ---
    resolution_filter = ""
    if resolution == "1080":
        resolution_filter = "scale=-1:1080[main_video]; "
    elif resolution == "720":
        resolution_filter = "scale=-1:720[main_video]; "
    elif resolution == "480":
        resolution_filter = "scale=-1:480[main_video]; "
    # If "original", we don't add a scale filter.
    
    # --- Combine into filter_complex ---
    if resolution_filter:
        # We are rescaling. Input [0:v] is scaled, named [main_video]
        video_input_stream = "[main_video]"
        filter_complex = (
            f"[0:v]{resolution_filter}"
            f"[1:v]scale=100:-1[watermark]; "
            f"{video_input_stream}[watermark]{overlay_cmd}"
        )
    else:
        # No rescaling. Input [0:v] is used directly
        video_input_stream = "[0:v]"
        filter_complex = (
            f"[1:v]scale=100:-1[watermark]; "
            f"{video_input_stream}[watermark]{overlay_cmd}"
        )

    # --- Final Command ---
    # -c:a copy = copies audio stream (fast)
    # -preset veryfast = speeds up encoding. Remove if quality is bad.
    # -c:v libx264 = specifies video codec, good for compatibility
    command = (
        f'ffmpeg -i "{input_path}" -i logo.png '
        f'-filter_complex "{filter_complex}" '
        f'-c:a copy -c:v libx264 -preset veryfast "{output_path}"'
    )
    
    print(f"Running command: {command}") # For debugging in Termux
    return command


# ---=== HELPER FUNCTIONS (Unchanged) ===---

async def run_ffmpeg_command(command):
    """Runs an FFmpeg command and waits for it to complete."""
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        print(f"Error executing FFmpeg command:\n{stderr.decode()}")
        return False
    return True

def cleanup_files(paths):
    """Deletes files if they exist."""
    for path in paths:
        if path and os.path.exists(path):
            os.remove(path)

async def progress_bar(current, total, status_msg, start_time, action):
    """Updates the status message with a progress bar."""
    now = time.time()
    elapsed = now - start_time
    
    # Update only once per second, unless it's the final update
    if (now - getattr(progress_bar, "last_update", 0)) < 1 and current != total:
        return
    setattr(progress_bar, "last_update", now)
    
    if total == 0:
        # Avoid ZeroDivisionError if total size is unknown
        percentage = 0
        speed = 0
    else:
        percentage = current * 100 / total
        if elapsed == 0:
            elapsed = 1 # Avoid division by zero on speed calc
        speed = current / elapsed
    
    bar = "█" * int(percentage / 10) + "░" * (10 - int(percentage / 10))
    
    try:
        await status_msg.edit_text(
            f"**{action}...**\n"
            f"[{bar}] {percentage:.1f}%\n"
            f"`{current / 1024**2:.1f} MB` / `{total / 1024**2:.1f} MB`\n"
            f"Speed: `{speed / 1024**2:.1f} MB/s`\n"
        )
    except Exception as e:
        # Handle "message not modified" or other errors
        pass



# ---=== START THE BOT ===---
print("Bot is starting...")
app.run()
