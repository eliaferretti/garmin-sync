import io
import os
import zipfile
from pathlib import Path

import requests
from garminconnect import Garmin

import fit_to_csv

STATE_FILE = "last_activity.txt"

# Sampling interval in seconds for the records CSV (0 = auto).
CSV_INTERVAL = 0


def get_latest_activity(client):
   activities = client.get_activities(0, 1)
   if not activities:
      return None
   return str(activities[0]["activityId"])


def send_to_telegram(bot_token, chat_id, csv_text, filename, caption):
   url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
   files = {"document": (filename, csv_text.encode("utf-8"))}
   # Telegram rejects captions longer than 1024 characters.
   data = {"chat_id": chat_id, "caption": caption[:1024]}
   response = requests.post(url, data=data, files=files, timeout=60)
   response.raise_for_status()


def extract_fit(zip_data):
   with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
      for name in z.namelist():
         if name.lower().endswith(".fit"):
            return name, z.read(name)
   return None, None


def process_activity(client, activity_id, bot_token, chat_id):
   zip_data = client.download_activity(
      int(activity_id),
      dl_fmt=client.ActivityDownloadFormat.ORIGINAL
   )

   fit_filename, fit_bytes = extract_fit(zip_data)
   if not fit_bytes:
      print("No .fit file found in the archive.")
      return False

   result = fit_to_csv.build_csvs(
      fit_bytes,
      stem=Path(fit_filename).stem,
      interval=CSV_INTERVAL
   )

   send_to_telegram(
      bot_token,
      chat_id,
      result["records_csv"],
      result["records_name"],
      fit_to_csv.summary_text(result)
   )

   print(f"Sent {result['records_name']} "
         f"({result['rows_kept']} rows @ {result['interval']}s)")

   return True


def main():
   garmin_email = os.getenv("GARMIN_EMAIL")
   garmin_pass = os.getenv("GARMIN_PASS")
   bot_token = os.getenv("BOT_TOKEN")
   chat_id = os.getenv("CHAT_ID")

   if not all([garmin_email, garmin_pass, bot_token, chat_id]):
      print("Missing environment variables. Please check your setup.")
      return

   try:
      client = Garmin(garmin_email, garmin_pass)
      client.login()
   except Exception as e:
      print(f"Failed to login to Garmin: {e}")
      return

   latest_id = get_latest_activity(client)
   if not latest_id:
      print("No activities found.")
      return

   last_id = ""
   if os.path.exists(STATE_FILE):
      with open(STATE_FILE, "r") as f:
         last_id = f.read().strip()

   if latest_id == last_id:
      print("No new activity.")
      return

   if not process_activity(client, latest_id, bot_token, chat_id):
      print("Activity not sent, state file left unchanged.")
      return

   with open(STATE_FILE, "w") as f:
      f.write(latest_id)

   print(f"Done: activity {latest_id}")


if __name__ == "__main__":
   main()
