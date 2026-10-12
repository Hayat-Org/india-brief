"""One-time helper: turn a short-lived Facebook user token into the permanent
Page token, Page ID and Instagram ID you need. Run it on your own computer:

    pip install requests
    python meta_helper.py

It asks for your App ID, App Secret and the short-lived token from the Graph API
Explorer. Nothing is stored or sent anywhere except to graph.facebook.com.
"""
import getpass

import requests

VER = "v23.0"
G = f"https://graph.facebook.com/{VER}"


def get(path, **params):
    r = requests.get(f"{G}/{path}", params=params, timeout=30)
    data = r.json()
    if r.status_code >= 300 or "error" in data:
        raise SystemExit(f"Meta said: {data.get('error', data)}")
    return data


def main():
    app_id = input("App ID: ").strip()
    app_secret = getpass.getpass("App Secret (hidden): ").strip()
    short = getpass.getpass("Short-lived user token (hidden): ").strip()

    long_user = get("oauth/access_token", grant_type="fb_exchange_token",
                    client_id=app_id, client_secret=app_secret, fb_exchange_token=short)
    pages = get("me/accounts", access_token=long_user["access_token"],
                fields="id,name,access_token,instagram_business_account")["data"]
    if not pages:
        raise SystemExit("No Pages found. Did you tick your Page when granting permissions?")
    print("\nPages you manage:")
    for i, p in enumerate(pages, 1):
        print(f"  {i}. {p['name']} (id {p['id']})")
    p = pages[int(input("Pick a number: ")) - 1]
    ig = (p.get("instagram_business_account") or {}).get("id")

    print("\n--- Put these in GitHub Secrets ---")
    print(f"FB_PAGE_ID    = {p['id']}")
    print(f"FB_PAGE_TOKEN = {p['access_token']}")
    print(f"IG_USER_ID    = {ig or '(none: link your Instagram professional account to the Page first)'}")
    print("\nPage tokens obtained from a long-lived user token do not expire.")
    print("Never share these values with anyone, including me.")


if __name__ == "__main__":
    main()
