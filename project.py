import os
from collections import defaultdict
from html.entities import name2codepoint
from dateutil.parser import parse
from datetime import datetime
import re

from utils import fetch_labels_mapping, fetch_allowed_labels, convert_label


class Project:

    def __init__(self, name, doneStatusCategoryId, jiraBaseUrl):
        self.name = name
        self.doneStatusCategoryId = doneStatusCategoryId
        self.jiraBaseUrl = jiraBaseUrl
        self._project = {'Milestones': defaultdict(int), 'Components': defaultdict(
            int), 'Labels': defaultdict(int), 'Types': defaultdict(int), 'Issues': []}

        self.labels_mapping = fetch_labels_mapping()
        self.approved_labels = fetch_allowed_labels()

    def get_milestones(self):
        return self._project['Milestones']

    def get_components(self):
        return self._project['Components']

    def get_issues(self):
        return self._project['Issues']

    def get_types(self):
        return self._project['Types']

    def get_all_labels(self):
        merge = self._project['Components'].copy()
        merge.update(self._project['Labels'])
        merge.update(self._project['Types'])
        merge.update({'imported-jira-issue': 0})
        return merge

    def get_labels(self):
        merge = self._project['Labels'].copy()
        merge.update({'imported-jira-issue': 0})
        return merge

    def add_item(self, item):
        itemProject = self._projectFor(item)
        if itemProject != self.name:
            print('Skipping item ' + item.key.text + ' for project ' +
                  itemProject + ' current project: ' + self.name)
            return

        self._append_item_to_project(item)

        self._add_milestone(item)

        self._add_labels(item)

        self._add_subtasks(item)

        self._add_parenttask(item)

        self._add_comments(item)

        self._add_relationships(item)

    def prettify(self):
        def hist(h):
            for key in h.keys():
                print(('%30s (%5d): ' + h[key] * '#') % (key, h[key]))
            print

        print(self.name + ':\n  Milestones:')
        hist(self._project['Milestones'])
        print('  Types:')
        hist(self._project['Types'])
        print('  Components:')
        hist(self._project['Components'])
        print('  Labels:')
        hist(self._project['Labels'])
        print
        print('Total Issues to Import: %d' % len(self._project['Issues']))

    def _projectFor(self, item):
        try:
            result = item.project.get('key')
        except AttributeError:
            result = item.key.text.split('-')[0]
        return result

    def _append_item_to_project(self, item):
        # todo assignee
        closed = str(item.statusCategory.get('id')) == self.doneStatusCategoryId
        closed_at = ''
        if closed:
            try:
                closed_at = self._convert_to_iso(item.resolved.text)
            except AttributeError:
                pass

        # TODO: ensure item.assignee/reporter.get('username') to avoid "JENKINSUSER12345"
        # TODO: fixit in gh issues

        body = self._htmlentitydecode(item.description.text)
        # metadata: original author & link

        reporter_name = str(item.reporter) if item.reporter else "Unknown"

        body += (
            f'\n\n---\n<details><summary><i>Originally reported by '
            f'<a title="{reporter_name}" href="{self.jiraBaseUrl}/secure/ViewProfile.jspa?name={reporter_name}">'
            f'{reporter_name}</a>, imported from: '
            f'<a href="{self.jiraBaseUrl}/browse/{item.key.text}" target="_blank">'
            f'{item.title.text[item.title.text.index("]") + 2:]}</a></i></summary>'
        )
        # metadata: assignee
        body = body + '\n<i><ul>'

        # Handle assignee safely
        if item.assignee and item.assignee != 'Unassigned':
            assignee_name = str(item.assignee)  # Assume assignee is a string, not a dictionary
            body += (
                f'\n<li><b>assignee</b>: <a title="{assignee_name}" '
                f'href="{self.jiraBaseUrl}/secure/ViewProfile.jspa?name={assignee_name}">'
                f'{assignee_name}</a></li>'
            )

        # Handle ticket type safely
        try:
            body += f'\n<li><b>type</b>: {item.type.text}</li>'
        except AttributeError:
            pass

        # Handle status safely
        try:
            body += f'\n<li><b>status</b>: {item.status}</li>'
        except AttributeError:
            pass

        # Handle priority safely
        try:
            body += f'\n<li><b>priority</b>: {item.priority}</li>'
        except AttributeError:
            pass

        # Handle resolution safely
        try:
            body += f'\n<li><b>resolution</b>: {item.resolution}</li>'
        except AttributeError:
            pass

        # Handle resolved date safely
        try:
            resolved_date = self._convert_to_iso(item.resolved.text)
            body += f'\n<li><b>resolved</b>: {resolved_date}</li>'
        except AttributeError:
            pass

        # Add imported date
        body += f'\n<li><b>imported</b>: {datetime.today().strftime("%Y-%m-%d")}</li>'
        
        # Extract custom field with ID for Epic Link
        custom_field_id = "customfield_10014"
        custom_field_value = None
        for customfield in item.customfields.findall('customfield'):
            if customfield.get('id') == custom_field_id:
                customfieldvalue = customfield.find('.//customfieldvalue')
                if customfieldvalue is not None:
                    custom_field_value = customfieldvalue.text
                break

        if custom_field_value:
            body += f'\n<li><b>Epic Link</b>: {custom_field_value}</li>'

        # Extract custom field with ID for Sprint
        custom_field_id = "customfield_10021"
        custom_field_value = None
        for customfield in item.customfields.findall('customfield'):
            if customfield.get('id') == custom_field_id:
                customfieldvalue = customfield.find('.//customfieldvalue')
                if customfieldvalue is not None:
                    custom_field_value = customfieldvalue.text
                break

        if custom_field_value:
            body += f'\n<li><b>Sprint</b>: {custom_field_value}</li>'

        # Extract custom field with ID for Story Points
        custom_field_id = "customfield_10024"
        custom_field_value = None
        for customfield in item.customfields.findall('customfield'):
            if customfield.get('id') == custom_field_id:
                customfieldvalue = customfield.find('.//customfieldvalue')
                if customfieldvalue is not None:
                    custom_field_value = customfieldvalue.text
                break

        if custom_field_value:
            body += f'\n<li><b>Story Points</b>: {custom_field_value}</li>'

        body += '\n</ul></i>\n</details>'


        # retrieve jira components and labels as github labels
        labels = []
        # for component in item.component:
        #     if os.getenv('JIRA_MIGRATION_INCLUDE_COMPONENT_IN_LABELS', 'true') == 'true':
        #         labels.append('jira-component:' + component.text.lower())
        #         labels.append(component.text.lower())

        labels.append(self._jira_type_mapping(item.type.text.lower()))
        
        for label in item.labels.findall('label'):
            converted_label = convert_label(label.text.strip().lower(), self.labels_mapping, self.approved_labels)
            if converted_label is not None:
                labels.append(converted_label)

        labels.append('imported-jira-issue')

        unique_labels = list(filter(None, self.get_labels().keys()))  # Filter out None values

        self._project['Issues'].append({'title': item.title.text,
                                        'key': item.key.text,
                                        'body': body,
                                        'created_at': self._convert_to_iso(item.created.text),
                                        'closed_at': closed_at,
                                        'updated_at': self._convert_to_iso(item.updated.text),
                                        'closed': closed,
                                        'labels': unique_labels,
                                        'comments': [],
                                        'duplicates': [],
                                        'is-duplicated-by': [],
                                        'is-related-to': [],
                                        'depends-on': [],
                                        'blocks': []
                                        })
        if not self._project['Issues'][-1]['closed_at']:
            del self._project['Issues'][-1]['closed_at']

    def _jira_type_mapping(self, issue_type):
        if issue_type == 'Epic':
            return 'Epic'
        if issue_type == 'Bug':
            return 'Bug'
        if issue_type == 'Defect':
            return 'Defect'
        if issue_type == 'Enhancement':
            return 'Enhancement'
        if issue_type == 'Story':
            return 'Story'
        if issue_type == 'Task':
            return 'Task'
        if issue_type == 'Sub-task':
            return 'Sub-task'

    def _convert_to_iso(self, timestamp):
        dt = parse(timestamp)
        return dt.isoformat()

    def _add_milestone(self, item):
        try:
            self._project['Milestones'][item.fixVersion.text] += 1
            # this prop will be deleted later:
            self._project['Issues'][-1]['milestone_name'] = item.fixVersion.text.trim()
        except AttributeError:
            pass

    def _add_labels(self, item):
        try:
            self._project['Components'][item.component.text] += 1
            tmp_l = item.component.text.trim()
            if tmp_l == 'Bug':
                tmp_l = 'bug'

            self._project['Issues'][-1]['labels'].append(tmp_l)
        except AttributeError:
            pass
        
        try:
            for label in item.labels.label:
                self._project['Labels'][label.text] += 1
                tmp_l = label.text.trim()
                if tmp_l == 'Bug':
                    tmp_l = 'bug'

                self._project['Issues'][-1]['labels'].append(tmp_l)
        except AttributeError:
            pass

        try:
            self._project['Types'][item.type.text] += 1
            tmp_l = item.type.text.trim()
            if tmp_l == 'Bug':
                tmp_l = 'bug'

            self._project['Issues'][-1]['labels'].append(tmp_l)
        except AttributeError:
            pass

    def _add_subtasks(self, item):
        try:
            subtaskList = ''
            for subtask in item.subtasks.subtask:
                subtaskList = subtaskList + '- ' + subtask + '\n'
            if subtaskList != '':
                print('-> subtaskList: ' + subtaskList)
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(item.created.text),
                     "body": 'Subtasks:\n\n' + subtaskList})
        except AttributeError:
            pass

    def _add_parenttask(self, item):
        try:
            parentTask = item.parent.text
            if parentTask != '':
                print('-> parentTask: ' + parentTask)
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(item.created.text),
                     "body": 'Subtask of parent task ' + parentTask})
        except AttributeError:
            pass

    def _add_comments(self, item):
        try:
            for comment in item.comments.comment:
                self._project['Issues'][-1]['comments'].append(
                    {"created_at": self._convert_to_iso(comment.get('created')),
                     "body": '<i><a href="' + self.jiraBaseUrl + '/secure/ViewProfile.jspa?name=' + comment.get('author') + '">' + comment.get('author') + '</a>:</i>\n' + self._htmlentitydecode(comment.text)
                     })
        except AttributeError:
            pass

    def _add_relationships(self, item):
        try:
            for issuelinktype in item.issuelinks.issuelinktype:
                for outwardlink in issuelinktype.outwardlinks:
                    for issuelink in outwardlink.issuelink:
                        for issuekey in issuelink.issuekey:
                            tmp_outward = outwardlink.get("description").replace(' ', '-')
                            if tmp_outward in self._project['Issues'][-1]:
                                self._project['Issues'][-1][tmp_outward].append(issuekey.text)
        except AttributeError:
            pass
        except KeyError:
            print('1. KeyError at ' + item.key.text)
        try:
            for issuelinktype in item.issuelinks.issuelinktype:
                for inwardlink in issuelinktype.inwardlinks:
                    for issuelink in inwardlink.issuelink:
                        for issuekey in issuelink.issuekey:
                            tmp_inward = inwardlink.get("description").replace(' ', '-')
                            if tmp_inward in self._project['Issues'][-1]:
                                self._project['Issues'][-1][tmp_inward].append(issuekey.text)
        except AttributeError:
            pass
        except KeyError:
            print('2. KeyError at ' + item.key.text)

        # for customfield in item.customfields.findall('customfield'):
        #     if customfield.get('key') == 'com.pyxis.greenhopper.jira:gh-epic-link':
        #         epic_key = customfield.customfieldvalues.customfieldvalue
        #         self._project['Issues'][-1]['epic-link'] = epic_key

    def _htmlentitydecode(self, s):
        if s is None:
            return ''
        s = s.replace(' ' * 8, '')
        return re.sub('&(%s);' % '|'.join(name2codepoint),
                      lambda m: chr(name2codepoint[m.group(1)]), s)
