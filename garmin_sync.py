import os
import io
import zipfile
import requests
from garminconnect import Garmin

def get_latest_activity(client):
   activities = client.get_activities(0, 1)
   if not activities:
      return None
   return str(activities[0]["activityId"])

def send_to_telegram(bot_token, chat_id, file_bytes, filename):
   url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
   files = {"document": (filename, file_bytes)}
   data = {"chat_id": chat_id}
   response = requests.post(url, data=data, files=files)
   response.raise_for_status()

def process_activity(client, activity_id, bot_token, chat_id):
   zip_data = client.download_activity(
      int(activity_id),
      dl_fmt=client.ActivityDownloadFormat.ORIGINAL
   )
   
   with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
      fit_filename = None
      for name in z.namelist():
         if name.endswith('.fit'):
            fit_filename = name
            break
            
      if not fit_filename:
         print("No .fit file found in the archive.")
         return False
         
      fit_bytes = z.read(fit_filename)
      
   send_to_telegram(bot_token, chat_id, fit_bytes, fit_filename)
   return True

def main():
   GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
   GARMIN_PASS = os.getenv("GARMIN_PASS")
   BOT_TOKEN = os.getenv("BOT_TOKEN")
   CHAT_ID = os.getenv("CHAT_ID")
   STATE_FILE = "last_activity.txt"
   
   if not all([GARMIN_EMAIL, GARMIN_PASS, BOT_TOKEN, CHAT_ID]):
      print("Missing environment variables. Please check your setup.")
      return

   try:
      client = Garmin(GARMIN_EMAIL, GARMIN_PASS)
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
      print("No new activity to process.")
      return

   print(f"New activity found: {latest_id}. Extracting .fit and sending...")
   
   try:
      success = process_activity(client, latest_id, BOT_TOKEN, CHAT_ID)
      if success:
         with open(STATE_FILE, "w") as f:
            f.write(latest_id)
         print("Successfully sent to Telegram!")
   except Exception as e:
      print(f"An error occurred during processing: {e}")

if __name__ == "__main__":
   main()