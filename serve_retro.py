from flask import Flask
from flask import request, render_template
from decimal import *

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import base64
import re
import requests

import sys
reload(sys)
sys.setdefaultencoding('utf-8')

# This script takes xml generated by Jira for a single sprint, and then reports statistics for the sprint
app = Flask(__name__)
HOURS_IN_DAY = 6


@app.route('/', methods=['GET', 'POST'])
@app.route('/retro/', methods=['GET', 'POST'])  # legacy
def serve():
    if request.method == 'POST':
        try:
            filename = request.files['xml_file']
            start_date = request.form['start']
            end_date = request.form['end']
            total_working_hours, data, unplanned, deferred, misestimated, done_but_time_left, no_deferral_assignee, testers, total_qa_spent, total_tests = retro(filename, start_date, end_date)
            return render_template('serve_retro.html', start_date=start_date, end_date=end_date, total_working_hours=total_working_hours,
                                   data=data, unplanned=unplanned, deferred=deferred, misestimated=misestimated, done_but_time_left=done_but_time_left,
                                   no_deferral_assignee=no_deferral_assignee, testers=testers, total_qa_spent=total_qa_spent, total_tests=total_tests)
        except Exception:
            import traceback
            return traceback.format_exc()
    else:
        return render_template('serve_retro.html')


def convert_to_time(time):
    if not time:
        return '0h'
    from math import floor
    hours = floor(time)
    minutes = floor(time * 60 % 60)

    if hours and minutes:
        return str(int(hours)) + 'h' + ' ' + str(int(minutes)) + 'm'
    elif hours:
        return str(int(hours)) + 'h'
    else:
        return str(int(minutes)) + 'm'


def retro(filename, start_date, end_date):

    tree = ET.parse(filename)
    root = tree.getroot()
    channel = root.find("channel")

    start_time = datetime.strptime(start_date, "%Y-%m-%d")
    end_time = datetime.strptime(end_date, "%Y-%m-%d")
    daygenerator = (start_time + timedelta(x + 1) for x in xrange((end_time - start_time).days + 1))
    working_days = sum(1 for day in daygenerator if day.weekday() < 5)

    total_working_hours = working_days * HOURS_IN_DAY

    retro = {}
    total_time_spent = 0
    total_time_estimate = 0
    total_time_left = 0
    total_time_estimate_done = 0
    total_time_spent_done = 0
    total_time_unplanned = 0
    total_time_spent_bug = 0
    unplanned = {}
    deferred = {}
    misestimated = {}

    done_but_time_left = []
    no_deferral_assignee = []

    testers = {}
    total_qa_spent = 0

    for item in channel.findall('item'):
        assignee = item.find('assignee').text
        title = item.find('title').text

        time_estimate = item.find('timeoriginalestimate')

        if time_estimate is not None:
            hours_estimate = Decimal(time_estimate.get("seconds")) / 3600
        else:
            hours_estimate = 0

        time_left = item.find('timeestimate')
        if time_left is not None:  # do not consider time left if already marked as done
            if item.find("resolution").text == 'Done':
                done_with_hours_left = Decimal(time_left.get("seconds")) / 3600
                hours_left = 0
            else:
                done_with_hours_left = 0
                hours_left = Decimal(time_left.get("seconds")) / 3600
        else:
            done_with_hours_left = 0
            hours_left = 0

        time_spent = item.find('timespent')
        if time_spent is not None:
            hours_spent = Decimal(time_spent.get("seconds")) / 3600
        else:
            hours_spent = 0

        type = item.find('type').text

        created_time = datetime.strptime(item.find('created').text, "%a, %d %b %Y %H:%M:%S +0800")

        if assignee in retro:
            record = retro[assignee]
        else:
            record = {"total_estimated": 0, "total_spent": 0, "total_left": 0, "total_estimated_done": 0, "total_spent_done": 0, "total_unplanned": 0, "total_spent_bug": 0}
            retro[assignee] = record

        total_time_estimate += hours_estimate
        total_time_left += hours_left
        total_time_spent += hours_spent

        record["total_estimated"] += hours_estimate
        record["total_spent"] += hours_spent
        record["total_left"] += hours_left

        if type == "Bug":
            total_time_spent_bug += hours_spent
            record["total_spent_bug"] += hours_spent

        time_diff = created_time - start_time

        link = item.find("link").text
        type_url = item.find("type").get("iconUrl")

        if time_diff > timedelta(days=1):  # created 1 day after start of sprint
            total_time_unplanned += hours_spent
            record["total_unplanned"] += hours_spent

            unplanned_entry = {"title": title, "assignee": assignee, "hours_left": convert_to_time(hours_left), "hours_spent": convert_to_time(hours_spent), "link": link, "created": item.find('created').text, "icon": type_url}
            if assignee in unplanned:
                unplanned[assignee].append(unplanned_entry)
            else:
                unplanned[assignee] = [unplanned_entry]

        if item.find("resolution").text == 'Done':
            total_time_estimate_done += hours_estimate
            total_time_spent_done += hours_spent
            record["total_estimated_done"] += hours_estimate
            record["total_spent_done"] += hours_spent
            if done_with_hours_left > 0:  # this is a strange case
                done_but_time_left.append({"title": title, "assignee": assignee, "hours_left": convert_to_time(done_with_hours_left), "link": link, "updated": item.find('updated').text})

            if hours_estimate > 0.1 and abs(hours_spent - hours_estimate) / hours_estimate > .20:  # 60 seconds is used for QA.
                misestimated_entry = {"title": title, "assignee": assignee, "hours_estimated": convert_to_time(hours_estimate), "hours_spent": convert_to_time(hours_spent), "over_by": convert_to_time(hours_spent - hours_estimate), "diff": int(round((hours_spent - hours_estimate) / hours_estimate, 2) * 100), "link": link, "created": item.find('created').text, "icon": type_url}
                if assignee in misestimated:
                    misestimated[assignee].append(misestimated_entry)
                else:
                    misestimated[assignee] = [misestimated_entry]

        elif item.find("resolution").text == "Unresolved":
            deferred_entry = {"title": title, "assignee": assignee, "hours_left": convert_to_time(hours_left), "link": link, "updated": item.find('updated').text}
            if assignee in deferred:
                deferred[assignee].append(deferred_entry)
            else:
                deferred[assignee] = [deferred_entry]

        qa_hours = 0
        qa_tests = 0
        total_tests = 0
        tester = None
        custom_fields = item.find("customfields")
        if custom_fields is not None:
            for custom_field in custom_fields.findall('customfield'):
                if custom_field.find("customfieldname").text == "Tester":
                    tester = custom_field.find("customfieldvalues").find("customfieldvalue").text.capitalize()
                if custom_field.find("customfieldname").text == "QA Hours":
                    qa_hours = float(custom_field.find("customfieldvalues").find("customfieldvalue").text)
                if custom_field.find("customfieldname").text == "Automated Tests":
                    qa_tests = float(custom_field.find("customfieldvalues").find("customfieldvalue").text)

            if tester:
                if tester in testers:
                    testers[tester]["total_qa_hours"] += qa_hours
                    testers[tester]["total_tests"] += qa_tests
                else:
                    testers[tester] = {"total_qa_hours": qa_hours, "total_tests": qa_tests}
                total_qa_spent += qa_hours
                total_tests += qa_tests

    total_working_hours_team = total_working_hours * len(retro)

    data = []

    for assignee, record in retro.iteritems():

        try:
            data.append([assignee,
                         convert_to_time(record["total_estimated"]),
                         convert_to_time(record["total_estimated_done"]),
                         convert_to_time(record["total_spent_done"]),
                         convert_to_time(record["total_spent"]),
                         convert_to_time(record["total_left"]),
                         convert_to_time(record["total_unplanned"]),
                         convert_to_time(record["total_spent_bug"]),
                         int((Decimal(record["total_spent_done"]) / record["total_estimated_done"]) * 100),
                         int((Decimal(record["total_left"]) / record["total_estimated"]) * 100),
                         int((Decimal(record["total_unplanned"]) / record["total_estimated"]) * 100),
                         int((Decimal(record["total_spent"]) / total_working_hours) * 100),
                         int((Decimal(record["total_spent_bug"]) / record["total_spent_done"]) * 100)])

            if Decimal(record["total_left"]) == 0:
                no_deferral_assignee.append(assignee)
        except (ZeroDivisionError, InvalidOperation):
            # we do not consider staff with 0 hours, as they are probably not team members
            pass

    data.append(["Total",
                 convert_to_time(total_time_estimate),
                 convert_to_time(total_time_estimate_done),
                 convert_to_time(total_time_spent_done),
                 convert_to_time(total_time_spent),
                 convert_to_time(total_time_left),
                 convert_to_time(total_time_unplanned),
                 convert_to_time(total_time_spent_bug),
                 int((Decimal(total_time_spent_done) / total_time_estimate_done) * 100),
                 int((Decimal(total_time_left) / total_time_estimate) * 100),
                 int((Decimal(total_time_unplanned) / total_time_estimate) * 100),
                 int((Decimal(total_time_spent) / total_working_hours_team) * 100),
                 int((Decimal(total_time_spent_bug) / total_time_spent) * 100)])
    return total_working_hours, data, unplanned, deferred, misestimated, done_but_time_left, no_deferral_assignee, testers, total_qa_spent, total_tests


@app.route('/epic/', methods=['GET', 'POST'])
def serve_epic():
    if request.method == 'POST':
        try:
            start_date = request.form['start']
            end_date = request.form['end']
            project_name = request.form['project_name']
            total_points, developers, testers, epics, errors = analyze_epic(project_name, start_date, end_date)
            return render_template('epic_analysis.html', project_name=project_name, start_date=start_date, end_date=end_date, total_points=total_points, developers=developers, testers=testers, epics=epics, errors=errors)
        except Exception:
            import traceback
            return traceback.format_exc()
    else:
        return render_template('epic_analysis.html')


def analyze_epic(project_name, start_date, end_date):

    re.sub(r'\W+', '', project_name)  # sanitize the project_name
    project_name = project_name.upper()

    total_points = 0
    developers = {}
    testers = {}
    epics = []
    errors = []
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Basic ' + base64.b64encode('Gitlab:hFVdWwxyX55v')
    }

    jira_url = "https://jira.sidechef.cn/rest/api/2/search?maxResults=500&jql=project%20%3D%20" + project_name + "%20and%20type%3Depic%20and%20created%20>%20%27" + start_date + "%27%20and%20created%20<%20%27" + end_date + "%27%20and%20cf%5B10003%5D%3DDone"
    response = requests.get(jira_url, headers=headers)

    if response.status_code == 200:
        response_json = response.json()
        if 'issues' in response_json:
            issues = response_json['issues']
            for issue in issues:
                epic_key = issue['key']
                epic_name = issue['fields']['summary']
                epic_points = 0
                if issue['fields']['customfield_10206']:
                    epic_points = issue['fields']['customfield_10206']
                epic_created = issue['fields']['created']
                epic_updated = issue['fields']['updated']
                epic_total_dev_hours = 0.0
                epic_total_qa_hours = 0.0
                epic_devs = {}
                epic_reviewers = {}
                epic_qas = {}
                task_entries = []

                epic_url = "https://jira.sidechef.cn/rest/api/2/search?jql=project%20%3D%20" + project_name + "%20and%20\"Epic%20Link\"%20%3D%20" + epic_key
                epic_response = requests.get(epic_url, headers=headers)
                if epic_response.status_code == 200:
                    epic_response_json = epic_response.json()
                    if 'issues' in epic_response_json:
                        epic_issues = epic_response_json['issues']
                        for task in epic_issues:
                            task_key = task["key"]
                            task_name = task["fields"]["summary"]
                            task_developer = task["fields"]["assignee"]["displayName"]

                            if task["fields"]["customfield_10200"]:
                                task_reviewer = task["fields"]["customfield_10200"]["displayName"]
                            else:
                                task_reviewer = None

                            if task["fields"]["customfield_10204"]:
                                task_qa_time_spent = task["fields"]["customfield_10204"]
                            else:
                                task_qa_time_spent = 0

                            epic_total_qa_hours += task_qa_time_spent

                            if task["fields"]["customfield_10201"]:
                                task_tester = task["fields"]["customfield_10201"]["displayName"]
                            else:
                                if task_qa_time_spent > 0:  # no Tester, with hours in QA spent, so this is a QA task, we take the assignee
                                    task_tester = task["fields"]["assignee"]["displayName"]
                                else:
                                    task_tester = None

                            task_dev_time_spent = 0
                            if task["fields"]["timespent"]:
                                task_dev_time_spent = float(task["fields"]["timespent"]) / 3600

                            epic_total_dev_hours += task_dev_time_spent

                            if task_dev_time_spent > 0:
                                if task_developer in epic_devs:
                                    epic_devs[task_developer]['hours'] += task_dev_time_spent
                                else:
                                    epic_devs[task_developer] = {'hours': task_dev_time_spent}

                            if task_tester:
                                if task_tester in epic_qas:
                                    epic_qas[task_tester]['hours'] += task_qa_time_spent
                                else:
                                    epic_qas[task_tester] = {'hours': task_qa_time_spent}

                            if task_reviewer:
                                if task_reviewer in epic_reviewers:
                                    epic_reviewers[task_reviewer]['hours'] += task_dev_time_spent / 2
                                else:
                                    epic_reviewers[task_reviewer] = {'hours': task_dev_time_spent / 2}

                            task_entries.append({"key": task_key, "name": task_name.encode("utf-8"), "developer": task_developer, "tester": task_tester, "reviewer": task_reviewer, "dev_spent": task_dev_time_spent, "qa_spent": task_qa_time_spent})

                for dev in epic_devs:
                    epic_devs[dev]['points'] = epic_points / len(epic_devs)
                    if dev in developers:
                        developers[dev]["points"] += epic_points / len(epic_devs)
                    else:
                        developers[dev] = {"points": epic_points / len(epic_devs)}

                for qa in epic_qas:
                    epic_qas[qa]['points'] = epic_points / len(epic_qas)
                    if qa in testers:
                        testers[qa]["points"] += epic_points / len(epic_qas)
                    else:
                        testers[qa] = {"points": epic_points / len(epic_qas)}
                epic_total_hours = epic_total_dev_hours + epic_total_qa_hours

                epic = {"key": epic_key, "name": epic_name, "points": epic_points, "created": epic_created, "ended": epic_updated,
                        "total_hours": epic_total_hours, "dev_hours": epic_total_dev_hours, "qa_hours": epic_total_qa_hours, "devs": epic_devs,
                        "reviewers": epic_reviewers, "qas": epic_qas, "tasks": task_entries}

                epics.append(epic)

                total_points += epic_points

    return total_points, developers, testers, epics, errors


if __name__ == '__main__':
    app.run(debug=True)
