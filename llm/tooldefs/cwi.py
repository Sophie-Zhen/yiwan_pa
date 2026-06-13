"""CWI logbook tools (data/cwi_log.md).

Tool schemas + dispatch handlers for the cwi domain.
"""
from tools.cwi_log import (
    log_instructed_session,
    log_personal_climb,
    mark_recorded as cwi_mark_recorded,
    pending_entries as cwi_pending_entries,
    progress as cwi_progress,
)

HANDLERS = {
    "log_instructed_session": lambda a: log_instructed_session(
        date=a["date"], venue=a["venue"], detail=a["detail"], role=a.get("role", "assisted"),
        climbs_led=a.get("climbs_led", 0), reflective=a.get("reflective", True),
        large_public_facility=a.get("large_public_facility", False), notes=a.get("notes"),
    ),
    "log_personal_climb": lambda a: log_personal_climb(
        date=a["date"], venue=a["venue"], climbs_led=a.get("climbs_led", 0),
        detail=a.get("detail", ""), notes=a.get("notes"),
    ),
    "cwi_progress": lambda a: cwi_progress(),
    "cwi_list_pending": lambda a: cwi_pending_entries(),
    "cwi_mark_recorded": lambda a: cwi_mark_recorded(ids=a.get("ids")),
}


SCHEMAS = [{'name': 'log_instructed_session',
  'description': 'Record a brief CWI logbook entry for an instructing/assisting session Sophie '
                 'just delivered (top-rope taster, bouldering induction, group session, etc.), '
                 'as she works toward the Mountaineering Ireland Climbing Wall Instructor '
                 '(CWI) certificate. WORKFLOW: FIRST draft the DLOG reflective entry in chat '
                 "(English, instructor-log voice) for her to paste into MI's DLOG — that draft "
                 'is NOT stored. THEN call this to save only the brief metadata that powers '
                 "the evening 'enter into DLOG' reminder and the CWI progress count. status "
                 "starts 'pending' (not yet entered into the DLOG). One call per distinct "
                 'session (two sessions in a day = two calls).',
  'input_schema': {'type': 'object',
                   'properties': {'date': {'type': 'string',
                                           'description': 'Session date YYYY-MM-DD (use today '
                                                          'if she gives none).'},
                                  'venue': {'type': 'string',
                                            'description': "Wall / centre name, e.g. 'Awesome "
                                                           "Walls Dublin'. Used to count "
                                                           'distinct walls toward the >=2 '
                                                           'requirement.'},
                                  'detail': {'type': 'string',
                                             'description': 'Short session kind, e.g. '
                                                            "'top-rope taster', 'bouldering "
                                                            "induction'."},
                                  'role': {'type': 'string',
                                           'enum': ['led', 'assisted', 'supervised'],
                                           'description': "Sophie's role in the session. "
                                                          "Default 'assisted'."},
                                  'climbs_led': {'type': 'integer',
                                                 'description': 'Climbs SHE personally led '
                                                                'during the session, if any '
                                                                '(usually 0 when instructing). '
                                                                'Counts toward the 40-leads '
                                                                'requirement.'},
                                  'reflective': {'type': 'boolean',
                                                 'description': 'Whether a reflective comment '
                                                                'is written for this session '
                                                                'on DLOG. Default true (each '
                                                                'drafted entry is a reflective '
                                                                'comment); counts toward the '
                                                                '>=5-reflective requirement.'},
                                  'large_public_facility': {'type': 'boolean',
                                                            'description': 'True if the venue '
                                                                           'is a large public '
                                                                           'facility (e.g. '
                                                                           'Awesome Walls). At '
                                                                           'least one such '
                                                                           'venue is required '
                                                                           'among the '
                                                                           'instructing '
                                                                           'walls.'},
                                  'notes': {'type': 'string',
                                            'description': 'Optional short note (participant '
                                                           'count, anything to remember).'}},
                   'required': ['date', 'venue', 'detail']}},
 {'name': 'log_personal_climb',
  'description': "Record a brief CWI logbook entry for one of Sophie's PERSONAL climbing "
                 'visits (her own training, NOT instructing) — counts toward the CWI '
                 'personal-experience requirement (30 visits across >=3 walls, 40 climbs led). '
                 "Triggered by '今天在 X 爬了，led 了 N 条' and similar. status starts 'pending' (not "
                 'yet entered into MI DLOG).',
  'input_schema': {'type': 'object',
                   'properties': {'date': {'type': 'string',
                                           'description': 'Visit date YYYY-MM-DD (use today if '
                                                          'none).'},
                                  'venue': {'type': 'string',
                                            'description': 'Wall / centre name. Counts toward '
                                                           'distinct-walls (>=3).'},
                                  'climbs_led': {'type': 'integer',
                                                 'description': 'Number of climbs she led on '
                                                                'this visit (counts toward the '
                                                                '40-leads requirement). 0 if '
                                                                'none / bouldering only.'},
                                  'detail': {'type': 'string',
                                             'description': 'Optional short note on what she '
                                                            "climbed, e.g. 'lead + "
                                                            "bouldering'."},
                                  'notes': {'type': 'string',
                                            'description': 'Optional free-form note.'}},
                   'required': ['date', 'venue']}},
 {'name': 'cwi_progress',
  'description': "Report Sophie's progress toward the CWI assessment logbook requirements: "
                 'instructed sessions (target 15 across >=2 walls incl. a large public '
                 'facility, >=5 with reflective comments), personal climbing visits (target 30 '
                 'across >=3 walls), and climbs led (target 40). Returns done / target / '
                 "remaining for each. Use for '我的 CWI 进度怎么样 / 还差多少'. Targets are the official "
                 'MI numbers — counted, not estimated.',
  'input_schema': {'type': 'object', 'properties': {}, 'required': []}},
 {'name': 'cwi_list_pending',
  'description': "List CWI logbook entries still in 'pending' status — logged here but not yet "
                 "entered into Mountaineering Ireland's DLOG. Use for '哪些还没录' and to find the "
                 "id(s) to pass to cwi_mark_recorded when she's entered some but not all.",
  'input_schema': {'type': 'object', 'properties': {}, 'required': []}},
 {'name': 'cwi_mark_recorded',
  'description': 'Mark CWI logbook entries as entered into the MI DLOG, which stops the '
                 "evening reminder for them. Use when Sophie says she's recorded them ('录好了', "
                 "'都录进去了', 'logged it'). Omit ids to mark ALL pending entries (the common 'all "
                 "done' case); pass specific ids (from cwi_list_pending) when she's entered "
                 'only some.',
  'input_schema': {'type': 'object',
                   'properties': {'ids': {'type': 'array',
                                          'items': {'type': 'integer'},
                                          'description': 'Specific entry ids to mark recorded. '
                                                         'Omit to mark all pending entries.'}},
                   'required': []}}]
