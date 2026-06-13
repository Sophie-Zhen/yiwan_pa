"""Todo / inbox tools (data/inbox.md + data/archive.md).

Tool schemas + dispatch handlers for the todos domain.
"""
from datetime import datetime

from storage.markdown import (
    TERMINAL_STATUSES,
    Item,
    append_to_inbox,
    append_to_notes,
    find_item,
    move_to_archive,
    read_archive,
    read_inbox,
    set_item_status,
    skip_remaining_alerts,
    update_inbox_item,
)


def _item_payload(item):
    """Compact serialisation of an Item — drops empty fields, and deliberately
    excludes scheduler-internal alert fields the LLM must not see/edit."""
    return {
        k: v
        for k, v in {
            "title": item.title,
            "type": item.type,
            "mode": item.mode,
            "project": item.project,
            "due": item.due,
            "status": item.status,
            "tags": item.tags,
            "notes": item.notes,
        }.items()
        if v is not None
    }


def _items_to_payload(items):
    return [_item_payload(item) for item in items]


def _matches_to_payload(matches):
    return [{"location": loc, **_item_payload(item)} for loc, item in matches]


def _h_append_to_inbox(a):
    # Dispatch-layer guard against a duplicate-capture hallucination: refuse if
    # an item with the exact same title already exists in inbox.
    new_title = a["title"]
    for existing in read_inbox():
        if existing.title.lower() == new_title.lower():
            return {
                "ok": False,
                "reason": (
                    f"An item titled {existing.title!r} already exists in "
                    "inbox. Use update_inbox_item to modify it, or choose "
                    "a more specific title if you really intend a new item."
                ),
            }
    item = Item(
        title=new_title,
        created=datetime.now().strftime("%Y-%m-%d %H:%M"),
        status="pending",
        type=a.get("type"),
        mode=a.get("mode"),
        project=a.get("project"),
        due=a.get("due"),
        tags=a.get("tags"),
        notes=a.get("notes"),
        alerts=a.get("alerts"),
    )
    append_to_inbox(item)
    return {"ok": True, "title": item.title}


def _h_update_inbox_item(a):
    updated = update_inbox_item(a["title_substring"], a["field"], a["value"])
    if updated is None:
        return {"ok": False, "reason": "no item matched"}
    return {"ok": True, "title": updated.title, "field": a["field"], "value": a["value"]}


def _h_append_to_notes(a):
    updated = append_to_notes(a["title_substring"], a["value"])
    if updated is None:
        return {"ok": False, "reason": "no item matched"}
    return {"ok": True, "title": updated.title, "notes": updated.notes}


def _h_skip_remaining_alerts(a):
    item = skip_remaining_alerts(a["title_substring"])
    if item is None:
        return {"ok": False, "reason": "no item matched"}
    return {"ok": True, "title": item.title}


def _execute_set_status(title_substring, new_status):
    """Resolve the target item, enforce the sequential-project invariant, then
    write in place (non-terminal) or move to archive (terminal)."""
    matches = find_item(title_substring)
    if not matches:
        return {"ok": False, "reason": "no item matched"}
    target = None
    for loc, item in matches:
        if loc == "inbox":
            target = item
            break
    if target is None:
        return {"ok": False, "reason": "item is in archive, not inbox — cannot change its status"}

    if new_status == "in_progress" and target.project:
        project_record = None
        for _loc, it in find_item(target.project):
            if it.type == "project" and it.title == target.project:
                project_record = it
                break
        if project_record and project_record.mode == "sequential":
            siblings_in_progress = [
                it
                for it in read_inbox()
                if it.project == target.project
                and it.status == "in_progress"
                and it.title != target.title
            ]
            if siblings_in_progress:
                blocker = siblings_in_progress[0]
                return {
                    "ok": False,
                    "reason": (
                        f"sequential project '{target.project}' already has an "
                        f"in_progress step: '{blocker.title}'. Finish or pause "
                        "that step before starting another."
                    ),
                }

    if new_status in TERMINAL_STATUSES:
        moved = move_to_archive(target.title, new_status)
        if moved is None:
            return {"ok": False, "reason": "no item matched on move"}
        return {"ok": True, "title": moved.title, "status": new_status, "moved_to_archive": True}
    updated = set_item_status(target.title, new_status)
    if updated is None:
        return {"ok": False, "reason": "no item matched on set"}
    return {"ok": True, "title": updated.title, "status": new_status}


HANDLERS = {
    "read_inbox": lambda a: _items_to_payload(read_inbox()),
    "read_archive": lambda a: _items_to_payload(read_archive()),
    "append_to_inbox": _h_append_to_inbox,
    "update_inbox_item": _h_update_inbox_item,
    "append_to_notes": _h_append_to_notes,
    "find_item": lambda a: _matches_to_payload(find_item(a["title_substring"])),
    "skip_remaining_alerts": _h_skip_remaining_alerts,
    "set_status": lambda a: _execute_set_status(a["title_substring"], a["status"]),
}


SCHEMAS = [{'name': 'read_inbox',
  'description': 'Read all pending todo items currently in the inbox. Returns a list with '
                 'title, status, due, tags, and notes for each item.',
  'input_schema': {'type': 'object', 'properties': {}, 'required': []}},
 {'name': 'read_archive',
  'description': 'Read all completed or cancelled todo items in the archive.',
  'input_schema': {'type': 'object', 'properties': {}, 'required': []}},
 {'name': 'append_to_inbox',
  'description': 'Add a new pending item to the top of the inbox. Use this for: (a) a '
                 'standalone task — leave type/mode/project unset; (b) a multi-step project — '
                 "set type='project' and mode ('sequential' = steps must be done in order, "
                 "'parallel' = any order); (c) a step belonging to an existing project — set "
                 "project=<that project's title>. Steps of a project should be appended in the "
                 'order they will be executed (the storage order is the execution order).',
  'input_schema': {'type': 'object',
                   'properties': {'title': {'type': 'string',
                                            'description': 'Short title summarising the item.'},
                                  'type': {'type': 'string',
                                           'enum': ['project'],
                                           'description': "Set to 'project' when creating a "
                                                          'multi-step project record. Omit for '
                                                          'ordinary todos and for steps '
                                                          'belonging to a project.'},
                                  'mode': {'type': 'string',
                                           'enum': ['sequential', 'parallel'],
                                           'description': "Required when type='project'. "
                                                          "'sequential' = steps must progress "
                                                          'in order (only one in_progress at a '
                                                          "time). 'parallel' = steps may "
                                                          'proceed in any order.'},
                                  'project': {'type': 'string',
                                              'description': 'When this item is a step of a '
                                                             'project, set this to the parent '
                                                             "project's title. Do not set when "
                                                             'creating the project itself or a '
                                                             'standalone item.'},
                                  'due': {'type': 'string',
                                          'description': 'Optional due date or datetime in '
                                                         'YYYY-MM-DD or YYYY-MM-DD HH:MM '
                                                         'format.'},
                                  'tags': {'type': 'string',
                                           'description': 'Optional space-separated #category '
                                                          'or #category/sub tags.'},
                                  'notes': {'type': 'string',
                                            'description': 'Optional free-form context.'},
                                  'alerts': {'type': 'string',
                                             'description': 'Optional comma-separated list of '
                                                            'minutes-before-due offsets at '
                                                            'which the user wants push '
                                                            "reminders (e.g. '180,120' for "
                                                            "T-3h and T-2h, '30' for T-30min, "
                                                            "'0' for a push at the due time "
                                                            'itself). Only meaningful for '
                                                            'items whose due has HH:MM '
                                                            'precision. Set ONLY when the user '
                                                            'explicitly requests reminders '
                                                            "('提前 30 分钟', 'T-1h', '就 3 点提醒', "
                                                            "'remind me at the time'). Do NOT "
                                                            'set this by default — items with '
                                                            "no `alerts` simply don't trigger "
                                                            'T-N pushes; they still appear in '
                                                            'the morning / evening digest. '
                                                            'Translate hours to minutes (3h -> '
                                                            "180). Use '0' when the user wants "
                                                            'a push exactly at the due moment '
                                                            "('就三点'/'到点提醒'/'remind me at X "
                                                            "sharp'). For ambiguous phrasing "
                                                            "('提醒我' with no offset specified), "
                                                            'ask the user how far in '
                                                            'advance.'}},
                   'required': ['title']}},
 {'name': 'update_inbox_item',
  'description': "Update one non-status field of an existing inbox item, REPLACING the field's "
                 'current value. The item is matched by the first whose title contains '
                 'title_substring (case-insensitive). Use this for modifications such as '
                 'changing the due date, title, tags, or rewriting the notes from scratch. For '
                 'status changes use the set_status tool — this tool will refuse status '
                 'updates. IMPORTANT — notes is overwrite-only: if the user wants to ADD to '
                 "existing notes ('再加一项', '再补一条', 'append') rather than replace them, call "
                 'append_to_notes instead; using this tool with field=notes would silently '
                 'discard the existing notes value.',
  'input_schema': {'type': 'object',
                   'properties': {'title_substring': {'type': 'string',
                                                      'description': 'Substring of the target '
                                                                     "item's title "
                                                                     '(case-insensitive).'},
                                  'field': {'type': 'string',
                                            'enum': ['title', 'due', 'tags', 'notes', 'alerts'],
                                            'description': 'Which field to update. Status is '
                                                           'intentionally excluded — use '
                                                           'set_status. For alerts: value is a '
                                                           'comma-separated minute-offset list '
                                                           '(same format as at capture, e.g. '
                                                           "'60' or '180,120' or '0'); "
                                                           'updating alerts also resets the '
                                                           "item's fired-history so the new "
                                                           'declaration takes effect cleanly.'},
                                  'value': {'type': 'string',
                                            'description': 'New value for the field. REPLACES '
                                                           'the existing value — does not '
                                                           'append.'}},
                   'required': ['title_substring', 'field', 'value']}},
 {'name': 'append_to_notes',
  'description': "Append a line to an inbox item's notes WITHOUT overwriting the existing "
                 'content. Use this whenever the user wants to add to / extend / supplement '
                 "existing notes ('再加一项 X', '再补一条 Y', 'also note Z', 'append'). If the item "
                 "already has notes, the new value is joined with '; '; if notes was empty, "
                 'value becomes the new notes. Use update_inbox_item with field=notes only '
                 'when the user explicitly says to replace or rewrite the entire notes.',
  'input_schema': {'type': 'object',
                   'properties': {'title_substring': {'type': 'string',
                                                      'description': 'Substring of the target '
                                                                     "item's title "
                                                                     '(case-insensitive).'},
                                  'value': {'type': 'string',
                                            'description': 'Text to append. Will be joined '
                                                           "with the existing notes by '; ' if "
                                                           'notes already has content.'}},
                   'required': ['title_substring', 'value']}},
 {'name': 'find_item',
  'description': 'Search both inbox AND archive for items whose title contains the given '
                 'substring (case-insensitive). Use this to verify whether an item exists or '
                 "to look up its state. Do NOT infer 'item doesn't exist' from read_inbox "
                 'alone — read_inbox only returns pending items, while completed or cancelled '
                 'items live in archive. Returns a list of matches, each with location '
                 "('inbox' or 'archive') plus the item fields. Empty list means not found in "
                 'either file.',
  'input_schema': {'type': 'object',
                   'properties': {'title_substring': {'type': 'string',
                                                      'description': 'Substring of the target '
                                                                     "item's title "
                                                                     '(case-insensitive).'}},
                   'required': ['title_substring']}},
 {'name': 'skip_remaining_alerts',
  'description': 'Cancel any pending T-N push alerts for an inbox item without losing its '
                 'declared alerts configuration. Use this when the user replies to a '
                 "late-alert message with 'skip <item>' / '取消提醒' / 'don't remind me' for that "
                 'item — the user has already done the thing (or no longer wants the rest of '
                 'the pre-due pushes). Marks all declared alert offsets as already fired. No '
                 'effect on status, due, or the alerts declaration itself; only on which '
                 "offsets count as 'already pushed'. The item is matched by the first whose "
                 'title contains title_substring (case-insensitive).',
  'input_schema': {'type': 'object',
                   'properties': {'title_substring': {'type': 'string',
                                                      'description': 'Substring of the target '
                                                                     "item's title "
                                                                     '(case-insensitive).'}},
                   'required': ['title_substring']}},
 {'name': 'set_status',
  'description': "Change an inbox item's status. This is the only tool that may change status. "
                 "Allowed values: 'pending' (not yet started), 'in_progress' (currently being "
                 "worked on), 'done' (completed), 'cancelled' (abandoned). Behaviour: (a) "
                 'terminal statuses (done / cancelled) also move the item to archive.md — no '
                 'separate archive call needed; (b) when transitioning a step to in_progress, '
                 "if its parent project's mode is 'sequential', the tool refuses if another "
                 'step in the same project is already in_progress (only one step at a time in '
                 'a sequential project). When that happens, ask the user whether to finish or '
                 'pause the blocking step first.',
  'input_schema': {'type': 'object',
                   'properties': {'title_substring': {'type': 'string',
                                                      'description': 'Substring of the target '
                                                                     "item's title "
                                                                     '(case-insensitive).'},
                                  'status': {'type': 'string',
                                             'enum': ['pending',
                                                      'in_progress',
                                                      'done',
                                                      'cancelled'],
                                             'description': 'New status for the item.'}},
                   'required': ['title_substring', 'status']}}]
