# coding: utf-8

from flask import make_response
from flask import request
from flask import redirect
from flask import abort
from flask import render_template
from flask import jsonify
from flask import url_for
from flask import Response
from flask import send_file
from flask_jwt_extended import jwt_required, jwt_optional, create_access_token, get_jwt_identity
from werkzeug.security import safe_str_cmp
from werkzeug.security import generate_password_hash, check_password_hash
import simplejson as json
import os
import sys
from time import sleep
from time import time
import unicodecsv as csv
import shortuuid
import datetime
from threading import Thread
import requests

from app import app
from app import logger
from app import jwt
from app import db
from scenario import Scenario
from account import Account
from package import Package
from package import get_ids
from saved_scenario import SavedScenario
from saved_scenario import get_latest_scenario
from scenario import get_common_package_data
from scenario import get_clean_package_id
from util import jsonify_fast
from util import jsonify_fast_no_sort
from util import str2bool
from util import elapsed
from util import abort_json
from util import safe_commit
from util import TimingMessages
from util import get_ip
from util import response_json

from app import DEMO_PACKAGE_ID

# warm the cache
# print "warming the cache"
# start_time = time()
# Scenario(get_clean_package_id(None))
# print "done, took {} seconds".format(elapsed(start_time, 2))

@app.after_request
def after_request_stuff(resp):
    sys.stdout.flush()  # without this jason's heroku local buffers forever
    #support CORS
    resp.headers['Access-Control-Allow-Origin'] = "*"
    resp.headers['Access-Control-Allow-Methods'] = "POST, GET, OPTIONS, PUT, DELETE, PATCH"
    resp.headers['Access-Control-Allow-Headers'] = "Origin, X-Requested-With, Content-Type, Accept, Authorization, Cache-Control"
    resp.headers['Access-Control-Expose-Headers'] = "Authorization, Cache-Control"
    resp.headers['Access-Control-Allow-Credentials'] = "true"

    # make cacheable
    resp.cache_control.max_age = 300
    resp.cache_control.public = True

    return resp


@app.route('/', methods=["GET", "POST", "POST"])
def base_endpoint():
    return jsonify_fast({
        "version": "0.0.1",
        "msg": "Don't panic"
    })


# @app.route('/favicon.ico')
# def favicon():
#     return redirect(url_for("static", filename="img/favicon.ico", _external=True, _scheme='https'))

@app.route('/scenario/<scenario_id>/journal/<issn_l>', methods=['GET', 'POST'])
@jwt_required
def jump_scenario_issn_get(scenario_id, issn_l):
    my_saved_scenario = get_saved_scenario(scenario_id)
    scenario = my_saved_scenario.live_scenario
    my_journal = scenario.get_journal(issn_l)
    return jsonify_fast_no_sort({"_settings": scenario.settings.to_dict(), "journal": my_journal.to_dict_details()})

@app.route('/data/common/<package_id>', methods=['GET'])
def jump_data_package_id_get(package_id):
    secret = request.args.get('secret', "")
    if not safe_str_cmp(secret, os.getenv("JWT_SECRET_KEY")):
        abort_json(500, "Secret doesn't match, not getting package")

    response = get_common_package_data(package_id)

    return jsonify_fast_no_sort(response)


# Provide a method to create access tokens. The create_access_token()
# function is used to actually generate the token, and you can return
# it to the caller however you choose.
@app.route('/login', methods=["GET", 'POST'])
def login():
    my_timing = TimingMessages()

    request_source = request.args
    if request.is_json:
        request_source = request.json
    username = request_source.get('username', None)
    password = request_source.get('password', None)

    if not username:
        return abort_json(400, "Missing username parameter")
    if not password:
        return abort_json(400, "Missing password parameter")

    my_timing.log_timing("before db get for account")
    my_account = Account.query.filter(Account.username == username).first()
    my_timing.log_timing("after db get for account")

    if not my_account or not check_password_hash(my_account.password_hash, password):
        return abort_json(401, "Bad username or password")

    # Identity can be any data that is json serializable.  Include timestamp so is unique for each demo start.
    identity_dict = {
        "account_id": my_account.id,
        "login_uuid": shortuuid.uuid()[0:10],
        "created": datetime.datetime.utcnow().isoformat(),
        "is_demo_account": my_account.is_demo_account
    }
    print u"login with {}".format(identity_dict)
    access_token = create_access_token(identity=identity_dict)

    my_timing.log_timing("after create_access_token")

    return jsonify({"access_token": access_token, "_timing": my_timing.to_dict()})


# curl -s -X POST -H 'Accept: application/json' -H 'Content-Type: application/json' --data '{"username":"test","password":"password","rememberMe":false}' http://localhost:5004/login
#curl -H 'Accept: application/json' -H "Authorization: Bearer ${TOKEN}" http://localhost:5004/protected

# Protect a view with jwt_required, which requires a valid access token
# in the request to access.
@app.route('/protected', methods=['GET'])
@jwt_required
def protected():
    # Access the identity of the current user with get_jwt_identity
    identity_dict = get_jwt_identity()
    return jsonify({"logged_in_as": identity_dict["account_id"]})



def get_saved_scenario(scenario_id, debug_mode=False):
    if debug_mode:
        identity_dict = {"account_id": DEMO_PACKAGE_ID}
        is_demo_account = True
    else:
        identity_dict = get_jwt_identity()
        is_demo_account = (identity_dict["account_id"] == "demo")
    if is_demo_account:
        my_saved_scenario = SavedScenario.query.get(scenario_id)
        if not my_saved_scenario:
            my_saved_scenario = SavedScenario.query.get("demo")
            my_saved_scenario.scenario_id = scenario_id
    else:
        my_saved_scenario = SavedScenario.query.get(scenario_id)
    if not my_saved_scenario:
        abort_json(404, "Scenario not found")

    if not debug_mode and my_saved_scenario.package_real.account_id != identity_dict["account_id"]:
        if not my_saved_scenario.package_real.consortium_package_id:
            abort_json(401, "Not authorized to view this package")
        consortium_package = Package.query.filter(Package.package_id==my_saved_scenario.package_real.consortium_package_id).first()
        if consortium_package.account_id != identity_dict["account_id"]:
            abort_json(401, "Not authorized to view this package")


    my_saved_scenario.set_live_scenario(None)

    return my_saved_scenario


# from https://stackoverflow.com/a/51480061/596939
# class RunAsyncToRequestResponse(Thread):
#     def __init__(self, url_end):
#         Thread.__init__(self)
#         self.url_end = url_end
#
#     def run(self):
#         url_start = self.url_end.split("?")[0]
#         url = u"https://cdn.unpaywalljournals.org/{}".format(self.url_end)
#         print u"starting cache request for {}".format(url)
#         headers = {"Cache-Control": "public, max-age=31536000",
#                    "Cache-Tag": "common, common_{}".format(url_start)}
#         print 1/0 # clean up the tags before it is used
#         r = requests.get(url, headers=headers)
#         print u"cache request status code {} for {}".format(r.status_code, url_start)


@app.route('/account', methods=['GET'])
@jwt_required
def precache_account_get():
    identity_dict = get_jwt_identity()
    my_account = Account.query.get(identity_dict["account_id"])
    tags_list = ["account"]
    if identity_dict["is_demo_account"]:
        my_account.make_unique_demo_packages(identity_dict["login_uuid"])
        tags_list += ["account_demo"]
    tags_list += [u"package_{}".format(p.package_id) for p in my_account.unique_packages]

    url = u"https://cdn.unpaywalljournals.org/cache/account?jwt={}".format(get_jwt())
    print u"redirecting cache request to {}".format(url)
    headers = {"Cache-Control": "public, max-age=31536000",
               "Cache-Tag": ",".join(tags_list)}
    print headers
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return jsonify_fast_no_sort(r.json())
    return abort_json(r.status_code, "Problem.")

@app.route('/cache/account', methods=['GET'])
@jwt_required
def cached_account_get():
    my_timing = TimingMessages()

    identity_dict = get_jwt_identity()
    my_account = Account.query.get(identity_dict["account_id"])
    if identity_dict["is_demo_account"]:
        my_account.make_unique_demo_packages(identity_dict["login_uuid"])
    my_timing.log_timing("after getting account")

    account_dict = {
        "id": my_account.id,
        "name": my_account.display_name,
        "is_demo_account": my_account.is_demo_account,
        "packages": [package.to_dict_summary() for package in my_account.unique_packages],
    }
    my_timing.log_timing("after to_dict()")
    account_dict["_timing"] = my_timing.to_dict()

    return jsonify_fast(account_dict)


def get_jwt():
    if request.args and request.args.get("jwt", None):
        return request.args.get("jwt")
    if request.headers["Authorization"] and "Bearer " in request.headers["Authorization"]:
        return request.headers["Authorization"].replace("Bearer ", "")
    return None

@app.route('/package/<package_id>', methods=['GET'])
@jwt_required
def package_id_get(package_id):
    my_timing = TimingMessages()

    identity_dict = get_jwt_identity()

    if package_id.startswith("demo"):
        my_package = Package.query.get("demo")
        my_package.package_id = package_id
    else:
        my_package = Package.query.get(package_id)

    if not my_package:
        abort_json(404, "Package not found")

    if my_package.account_id != identity_dict["account_id"]:
        abort_json(401, "Not authorized to view this package")

    my_timing.log_timing("after getting package")

    my_jwt = get_jwt()

    # for scenario in my_package.unique_saved_scenarios:
        # RunAsyncToRequestResponse("scenario/{}?jwt={}".format(scenario.scenario_id, my_jwt)).start()
        # RunAsyncToRequestResponse("scenario/{}/slider?jwt={}".format(scenario.scenario_id, my_jwt)).start()
        # RunAsyncToRequestResponse("scenario/{}/table?jwt={}".format(scenario.scenario_id, my_jwt)).start()
        # RunAsyncToRequestResponse("scenario/{}/apc?jwt={}".format(scenario.scenario_id, my_jwt)).start()
    # RunAsyncToRequestResponse("package/{}?jwt={}".format(package_id, my_jwt)).start()

    my_timing.log_timing("after kicking off cache requests")

    package_dict = my_package.to_dict_summary()
    my_timing.log_timing("after my_package.to_dict_summary()")
    package_dict["scenarios"] = [saved_scenario.to_dict_definition() for saved_scenario in my_package.unique_saved_scenarios]
    my_timing.log_timing("after scenarios()")
    package_dict["_timing"] = my_timing.to_dict()

    return jsonify_fast(package_dict)

@app.route('/scenario/<scenario_id>', methods=['GET'])
@jwt_required
def scenario_id_get(scenario_id):
    my_timing = TimingMessages()
    my_saved_scenario = get_saved_scenario(scenario_id)
    my_timing.log_timing("after setting live scenario")
    response = my_saved_scenario.to_dict_definition()
    my_timing.log_timing("after to_dict()")
    response["_timing"] = my_timing.to_dict()
    return jsonify_fast(response)


@app.route('/scenario/<scenario_id>', methods=['POST'])
@app.route('/scenario/<scenario_id>/post', methods=['GET'])  # just for debugging
@jwt_required
def scenario_id_post(scenario_id):
    my_timing = TimingMessages()

    identity_dict = get_jwt_identity()

    scenario_input = request.get_json()
    if not scenario_input:
        scenario_input = request.args

    my_saved_scenario = SavedScenario.query.get(scenario_id)

    # check if demo account is ok
    if identity_dict["is_demo_account"]:
        if not scenario_id.startswith("demo"):
            abort_json(401, "Not authorized to view this package")
        if not my_saved_scenario:
            my_saved_scenario = SavedScenario(True, scenario_id, scenario_input)
            my_saved_scenario.scenario_id = scenario_id
    else:
        if not my_saved_scenario:
            abort_json(404, "Package not found")
        if my_saved_scenario.package.account_id != identity_dict["account_id"]:
            abort_json(401, "Not authorized to view this package")

    package_id = get_clean_package_id({"package": my_saved_scenario.package_id})
    my_live_scenario = Scenario(package_id, scenario_input)  # don't care about old one, just write new one
    my_saved_scenario.live_scenario = my_live_scenario

    my_saved_scenario.save_live_scenario_to_db(get_ip(request))

    my_newly_saved_scenario = get_saved_scenario(scenario_id)
    my_timing.log_timing("after re-getting live scenario")
    response = my_newly_saved_scenario.to_dict_definition()

    my_timing.log_timing("after to_dict()")
    response["_timing"] = my_timing.to_dict()

    return jsonify_fast(response)



@app.route('/scenario/<scenario_id>/summary', methods=['GET', 'POST'])
@jwt_required
def scenario_id_summary_get(scenario_id):
    pagesize = int(request.args.get("pagesize", 5000))
    my_timing = TimingMessages()
    my_saved_scenario = get_saved_scenario(scenario_id)
    my_timing.log_timing("after setting live scenario")
    my_timing.log_timing("after to_dict()")
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_summary())

@app.route('/scenario/<scenario_id>/journals', methods=['GET', 'POST'])
@app.route('/scenario/<scenario_id>/overview', methods=['GET', 'POST'])
@jwt_required
def scenario_id_overview_get(scenario_id):
    pagesize = int(request.args.get("pagesize", 5000))
    my_timing = TimingMessages()
    my_saved_scenario = get_saved_scenario(scenario_id)
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_overview(pagesize))

@app.route('/scenario/<scenario_id>/raw', methods=['GET', 'POST'])
@jwt_required
def scenario_id_raw_get(scenario_id):
    pagesize = int(request.args.get("pagesize", 5000))
    my_saved_scenario = get_saved_scenario(scenario_id)
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_raw(pagesize))

@app.route('/scenario/<scenario_id>/table', methods=['GET', 'POST'])
@jwt_required
def scenario_id_table_get(scenario_id):
    pagesize = int(request.args.get("pagesize", 5000))
    my_saved_scenario = get_saved_scenario(scenario_id)
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_table(pagesize))

@app.route('/scenario/<scenario_id>/slider', methods=['GET', 'POST'])
@jwt_required
def scenario_id_slider_get(scenario_id):
    pagesize = int(request.args.get("pagesize", 5000))
    my_saved_scenario = get_saved_scenario(scenario_id)
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_slider())

@app.route('/scenario/<scenario_id>/apc', methods=['GET', 'POST'])
@jwt_required
def scenario_id_apc_get(scenario_id):
    pagesize = int(request.args.get("pagesize", 5000))
    my_saved_scenario = get_saved_scenario(scenario_id)
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_apc(pagesize))


@app.route('/scenario/<scenario_id>/report', methods=['GET', 'POST'])
@jwt_required
def scenario_id_report_get(scenario_id):
    pagesize = int(request.args.get("pagesize", 5000))
    my_saved_scenario = get_saved_scenario(scenario_id)
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_report(pagesize))


def export_get(my_saved_scenario):

    table_dicts = my_saved_scenario.live_scenario.to_dict_export(5000)["journals"]

    filename = "export.csv"
    with open(filename, "w") as file:
        csv_writer = csv.writer(file, encoding='utf-8')
        meta_keys = table_dicts[0]["meta"].keys()
        keys = meta_keys + table_dicts[0]["table_row"].keys()
        csv_writer.writerow(keys)
        for table_dict in table_dicts:
            row = []
            for my_key in keys:
                if my_key in meta_keys:
                    if my_key in "issn_l":
                        # doing this hacky thing so excel doesn't format the issn as a date :(
                        row.append(u"issn:{}".format(table_dict["meta"][my_key]))
                    else:
                        row.append(table_dict["meta"][my_key])
                else:
                    row.append(table_dict["table_row"][my_key])
            csv_writer.writerow(row)

    with open(filename, "r") as file:
        contents = file.readlines()

    return contents


@app.route('/scenario/<scenario_id>/export.csv', methods=['GET', 'POST'])
@jwt_required
def scenario_id_export_csv_get(scenario_id):
    my_saved_scenario = get_saved_scenario(scenario_id)
    contents = export_get(my_saved_scenario)
    # return Response(contents, mimetype="text/text")
    return Response(contents, mimetype="text/csv")


@app.route('/scenario/<scenario_id>/export', methods=['GET', 'POST'])
@jwt_required
def scenario_id_export_get(scenario_id):
    my_saved_scenario = get_saved_scenario(scenario_id)
    contents = export_get(my_saved_scenario)
    return Response(contents, mimetype="text/text")

@app.route('/debug/export', methods=['GET'])
def debug_export_get():
    scenario_id = "demo-debug"
    my_saved_scenario = get_saved_scenario(scenario_id, debug_mode=True)
    contents = export_get(my_saved_scenario)
    return Response(contents, mimetype="text/text")



@app.route('/admin/change_password', methods=['GET'])
@app.route('/admin/change-password', methods=['GET'])
def admin_change_password():
    username = request.args.get('username')
    old_password = request.args.get('old_password')
    if not old_password:
        old_password = request.args.get('old-password')
    new_password = request.args.get('new_password')
    if not new_password:
        new_password = request.args.get('new-password')
    if not username or not old_password or not new_password:
        return abort_json(400, "Missing parameters:  need username, old-password, new-password")

    my_account = Account.query.filter(Account.username == username).first()

    secret = request.args.get('secret', "")
    if secret and safe_str_cmp(secret, os.getenv("JWT_SECRET_KEY")):
        pass
    else:
        if not my_account or not check_password_hash(my_account.password_hash, old_password):
            return abort_json(401, "Bad username or or old password")
    my_account.password_hash = generate_password_hash(new_password)
    safe_commit(db)

    return jsonify({'message': "Password updated successfully",
                    "username": username,
                    "display_name": my_account.display_name})


@app.route('/admin/register', methods=['GET'])
def admin_register_user():
    secret = request.args.get('secret', None)
    if not safe_str_cmp(secret, os.getenv("JWT_SECRET_KEY")):
        abort_json(500, "Secret doesn't match, not saving user in database")

    new_account = Account()
    new_account.username = request.args.get('username')
    new_account.password_hash = generate_password_hash(request.args.get('password'))
    new_account.display_name = request.args.get('name',  None)
    db.session.add(new_account)
    safe_commit(db)

    return jsonify({'message': 'User registered successfully',
                    "username": request.args.get('username')})


@app.route('/debug/journal/<issn_l>', methods=['GET'])
def jump_debug_issn_get(issn_l):
    subscribe = str2bool(request.args.get('subscribe', "false"))
    scenario_id = "demo-debug"
    my_saved_scenario = get_saved_scenario(scenario_id, debug_mode=True)
    scenario = my_saved_scenario.live_scenario
    my_journal = scenario.get_journal(issn_l)
    if subscribe:
        my_journal.subscribed = True
    if not my_journal:
        abort_json(404, "journal not found")
    return jsonify_fast_no_sort({"_settings": scenario.settings.to_dict(), "journal": my_journal.to_dict_details()})

@app.route('/debug/scenario/table', methods=['GET'])
def jump_debug_table_get():
    scenario_id = "demo-debug"
    my_saved_scenario = get_saved_scenario(scenario_id, debug_mode=True)
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_table(5000))

@app.route('/debug/scenario/slider', methods=['GET'])
def jump_debug_slider_get():
    scenario_id = "demo-debug"
    my_saved_scenario = get_saved_scenario(scenario_id, debug_mode=True)
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_slider())

@app.route('/debug/scenario/apc', methods=['GET'])
def jump_debug_apc_get():
    scenario_id = "demo-debug"
    my_saved_scenario = get_saved_scenario(scenario_id, debug_mode=True)
    return jsonify_fast_no_sort(my_saved_scenario.live_scenario.to_dict_apc(5000))

@app.route('/debug/counter/<package_id>', methods=['GET'])
def jump_debug_counter_package_id(package_id):
    secret = request.args.get('secret', "")
    if not secret or not  safe_str_cmp(secret, os.getenv("JWT_SECRET_KEY")):
        return abort_json(401, "Not authorized, need secret.")

    my_package = Package.query.get(package_id)
    response = my_package.get_package_counter_breakdown()
    return jsonify_fast_no_sort(response)

@app.route('/debug/counter/<diff_type>/<package_id>', methods=['GET'])
def jump_debug_counter_diff_type_package_id(diff_type, package_id):
    secret = request.args.get('secret', "")
    if not secret or not  safe_str_cmp(secret, os.getenv("JWT_SECRET_KEY")):
        return abort_json(401, "Not authorized, need secret.")

    my_package = Package.query.get(package_id)
    attribute_name = getattr(my_package, "get_{}".format(diff_type))
    rows = attribute_name
    for row in rows:
        journal_string = row.get("title", "") or ""
        journal_string = journal_string.lower()
        journal_string = journal_string.decode("utf-8")
        journal_string = journal_string.replace(u" ", u"-")
        row["url"] = u"https://www.journals.elsevier.com/{}".format(journal_string)
    return jsonify_fast_no_sort({"count": len(rows), "list": rows})


@app.route('/debug/ids', methods=['GET'])
def jump_debug_ids():
    secret = request.args.get('secret', "")
    if not secret or not  safe_str_cmp(secret, os.getenv("JWT_SECRET_KEY")):
        return abort_json(401, "Not authorized, need secret.")

    response = get_ids()
    return jsonify_fast(response)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5004))
    app.run(host='0.0.0.0', port=port, debug=True, threaded=True)

















