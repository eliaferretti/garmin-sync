import argparse
import io
import os
import sys
import zipfile
from pathlib import Path

import requests
from garminconnect import Garmin

import fit_to_csv

STATE_FILE = "last_activity.txt"

# Sampling interval in seconds for the records CSV (0 = auto).
CSV_INTERVAL = 0

# How far back to look for activities missed by skipped runs. GitHub's
# scheduler drops runs when it is busy, so more than one activity can pile
# up between two successful syncs.
LOOKBACK = 20


def get_pending_activities(client, last_id):
   """Activity IDs not sent yet, oldest first.

   Garmin hands out activity IDs in increasing order, so anything above the
   stored ID is new to us. That also survives an activity being deleted on
   Garmin's side, which a position-in-the-list comparison would not.
   """
   activities = client.get_activities(0, LOOKBACK)
   ids = sorted(int(a["activityId"]) for a in activities)

   if not ids:
      return []

   # No usable state (first run, or a hand-edited file): send only the
   # newest one instead of flooding the chat with the whole lookback.
   if not last_id.isdigit():
      return [str(ids[-1])]

   return [str(i) for i in ids if i > int(last_id)]


def get_recent_activities(client, count):
   """The `count` most recent activity IDs, oldest first.

   Ignores the state file: this backs the on-demand resend command, whose
   whole point is to send something that was already sent.
   """
   count = max(1, min(count, LOOKBACK))
   activities = client.get_activities(0, count)
   return [str(i) for i in sorted(int(a["activityId"]) for a in activities)]


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


def parse_args(argv=None):
   parser = argparse.ArgumentParser(
      description="Send new Garmin activities to Telegram as CSV."
   )
   parser.add_argument(
      "--last", nargs="?", type=int, const=1, metavar="N",
      help=f"re-send the N most recent activities (default 1, max {LOOKBACK}) "
           "even if they were sent before, instead of syncing what is new"
   )
   args = parser.parse_args(argv)

   if args.last is not None and args.last < 1:
      parser.error("--last needs a positive count")

   return args


def main():
   args = parse_args()

   bot_token = os.getenv("BOT_TOKEN")
   chat_id = os.getenv("CHAT_ID")
   email = os.getenv("GARMIN_EMAIL")
   password = os.getenv("GARMIN_PASS")

   missing = [name for name, value in
              (("BOT_TOKEN", bot_token), ("CHAT_ID", chat_id)) if not value]

   # GARMINTOKENS holds a saved session; Garmin.login() picks it up on its
   # own. Credentials are only needed when there is no saved session.
   if not os.getenv("GARMINTOKENS") and not (email and password):
      missing.append("GARMINTOKENS (or GARMIN_EMAIL + GARMIN_PASS)")

   if missing:
      sys.exit(f"Missing environment variables: {', '.join(missing)}")

   try:
      client = Garmin(email, password)
      client.login()
   except Exception as e:
      sys.exit(f"Failed to login to Garmin: {e}")

   last_id = ""
   if os.path.exists(STATE_FILE):
      with open(STATE_FILE, "r") as f:
         last_id = f.read().strip()

   if args.last is not None:
      pending = get_recent_activities(client, args.last)
      if not pending:
         sys.exit("No activities found.")
   else:
      pending = get_pending_activities(client, last_id)
      if not pending:
         print(f"No new activity (last sent was {last_id or 'none'}).")
         return

   print(f"{len(pending)} activity(ies) to send: {', '.join(pending)}")

   # Oldest first, and the state file is written after every single send so
   # that a failure halfway through does not re-send what already went out.
   sent = []
   for activity_id in pending:
      if not process_activity(client, activity_id, bot_token, chat_id):
         sys.exit(f"Activity {activity_id} not sent, stopping at {last_id}.")
      sent.append(activity_id)

      # Never move the marker backwards: --last can replay an old activity,
      # and the next cron run must not then re-send everything after it.
      if not last_id.isdigit() or int(activity_id) > int(last_id):
         last_id = activity_id
         with open(STATE_FILE, "w") as f:
            f.write(last_id)

   print(f"Done: sent {', '.join(sent)}")


if __name__ == "__main__":
   main()
