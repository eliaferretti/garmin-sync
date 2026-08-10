#!/usr/bin/env python3
"""
Log in to Garmin from this machine and print a saved session token.

GitHub's runners share IPs with the whole world, so Garmin's WAF answers
their login attempts with 403/429. Logging in from your own connection and
handing the resulting token to CI sidesteps the login endpoint entirely.

    python make_garmin_token.py

Copy the printed blob into a GitHub secret named GARMINTOKENS
(repo -> Settings -> Secrets and variables -> Actions).

The token is as sensitive as your password: it is full access to your
Garmin account. Do not commit it. It lasts about a year; when the workflow
starts failing to log in, run this again and update the secret.
"""

import getpass
import sys

from garminconnect import Garmin


def main():
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")

    client = Garmin(email, password)

    try:
        client.login()
    except Exception as e:
        sys.exit(f"Login failed: {e}")

    tokens = client.client.dumps()

    print("\n--- copy everything between the lines into the GARMINTOKENS secret ---")
    print(tokens)
    print("--- end ---")


if __name__ == "__main__":
    main()
