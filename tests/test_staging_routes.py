import pytest
import requests
import json
import os
import re
# import heroku3

from schemas import user_schema, user_permissions_schema
from response_test import ResponseTest, dev_request_url, assert_schema
from views import app

url_base = os.environ["UNSUB_TEST_URL_STAGING"]

def skip_if_down():
    if not requests.get(url_base):
        pytest.skip("Unsub Staging API is down")

# def staging_conn():
#     # create client
#     heroku_conn = heroku3.from_key(os.environ['HEROKU_KEY_OURRESEARCH'])
#     # for staging
#     app = heroku_conn.apps()[os.environ['HEROKU_UNSUB_APPNAME_STAGING']]
#     # get process formation
#     proc = app.process_formation()['web']
#     return proc

# def staging_on(con):
#     con.scale(1)
#     return print("Unsub staging on")

# def staging_off(con):
#     con.scale(0)
#     return print("Unsub staging off")

@pytest.fixture
def fetch_jwt():
    skip_if_down()
    res = requests.post(
        url_base + "/user/login",
        json={
            "password": os.environ["UNSUB_USER1_PWD"],
            "email": os.environ["UNSUB_USER1_EMAIL"],
        },
    )
    return res.json()["access_token"]

# con = staging_conn()
# staging_on(con)
# jwt = fetch_jwt()

def test_staging_api_root():
    skip_if_down()
    x = requests.get(url_base)
    assert x.status_code == 200
    assert x.json() == {"msg": "Don't panic", "version": "0.0.1"}


def test_staging_api_login():
    skip_if_down()
    res = requests.post(
        url_base + "/user/login",
        json={
            "password": os.environ["UNSUB_USER1_PWD"],
            "email": os.environ["UNSUB_USER1_EMAIL"],
        },
    )
    tok = res.json()["access_token"]
    assert res.status_code == 200
    assert isinstance(tok, str)

def test_staging_api_user_me(fetch_jwt):
    res = requests.get(
        url_base + "/user/me", headers={"Authorization": "Bearer " + fetch_jwt}
    )
    assert res.status_code == 200
    assert isinstance(res.json(), dict)

    # with app.app_context():
    # Is the user data of the right shape and types?
    assert_schema(res.json(), user_schema, "/user/me")

def test_staging_api_user_permissions(fetch_jwt):
    res = requests.get(
        url_base + "/user-permissions", headers={"Authorization": "Bearer " + fetch_jwt},
        json={
            "user_id": os.environ["UNSUB_USER1_ID"],
            "institution_id": os.environ["UNSUB_USER1_INSTITUTION_ID"],
            "email": os.environ["UNSUB_USER1_EMAIL"],
        },
    )
    assert res.status_code == 200
    assert isinstance(res.json(), dict)

    # with app.app_context():
    # Is the user-permissions data of the right shape and types?
    assert_schema(res.json(), user_permissions_schema, "/user-permissions")

def test_staging_api_account(fetch_jwt):
    res = requests.get(
        url_base + "/account", headers={"Authorization": "Bearer " + fetch_jwt},
    )
    assert res.status_code == 404
    assert re.match("Removed. Use /user/me", res.json()["message"])

# staging_off(con)
