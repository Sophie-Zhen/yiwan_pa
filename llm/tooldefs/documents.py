"""Document fact-sheet + Q&A tools (data/documents/).

Tool schemas + dispatch handlers for the documents domain.
"""
from tools.documents import list_documents, read_document, save_document

HANDLERS = {
    "save_document": lambda a: save_document(
        name=a["name"], doc_type=a["doc_type"], fact_sheet=a["fact_sheet"],
        file=a.get("file"), source_date=a.get("source_date"), expiry=a.get("expiry"),
    ),
    "list_documents": lambda a: list_documents(),
    "read_document": lambda a: read_document(name=a["name"]),
}


SCHEMAS = [{'name': 'save_document',
  'description': "Persist a document's fact-sheet after extracting it from a PDF. WORKFLOW: "
                 "when a PDF arrives (the message is tagged '[文档 PDF: <name>，原件已存为 "
                 "<saved_name>]'), FIRST read it and reply with the extracted fact-sheet — key "
                 'facts (for insurance: 保单号, 保费, 免赔额/excess, 保额上限, 主要除外, 起止/到期日; adapt fields '
                 'to the doc type) plus a short summary — and ask Sophie to confirm. Only call '
                 'save_document AFTER she confirms (or after applying her fixes). Extract '
                 'GENEROUSLY: this is a once-a-year read, and later questions are answered '
                 'from this fact-sheet, not the PDF — capture anything she might ask later. '
                 'Pass `file` = the <saved_name> from the message tag so the original can be '
                 'found. If the document is a renewable contract (insurance/energy) with an '
                 'expiry, also offer to add it to contract tracking via add_contract.',
  'input_schema': {'type': 'object',
                   'properties': {'name': {'type': 'string',
                                           'description': "Document name, e.g. 'AXA 车险保单 "
                                                          "2026'."},
                                  'doc_type': {'type': 'string',
                                               'enum': ['insurance',
                                                        'car_insurance',
                                                        'home_insurance',
                                                        'warranty',
                                                        'manual',
                                                        'contract',
                                                        'statement',
                                                        'other']},
                                  'fact_sheet': {'type': 'string',
                                                 'description': 'The extracted fact-sheet body '
                                                                'as rich markdown (key facts + '
                                                                'summary). Multi-line is '
                                                                'fine.'},
                                  'file': {'type': 'string',
                                           'description': "The stored original's filename (the "
                                                          '<saved_name> from the PDF message '
                                                          'tag).'},
                                  'source_date': {'type': 'string',
                                                  'description': 'Optional document/ingest '
                                                                 'date YYYY-MM-DD; defaults to '
                                                                 'today.'},
                                  'expiry': {'type': 'string',
                                             'description': 'Optional expiry/renewal date '
                                                            'YYYY-MM-DD if the document has '
                                                            'one.'}},
                   'required': ['name', 'doc_type', 'fact_sheet']}},
 {'name': 'list_documents',
  'description': 'List stored documents (name, type, expiry, source_date) from their '
                 "fact-sheet headers — cheap, does not load the bodies. Use for '我有哪些文档 / "
                 "存了哪些单子' and to find which document a question is about before calling "
                 'read_document.',
  'input_schema': {'type': 'object', 'properties': {}, 'required': []}},
 {'name': 'read_document',
  'description': "Return a stored document's full fact-sheet to answer a question about it "
                 "(e.g. '我的车险免赔额多少', '房屋险保额上限'). Match by name/type substring. This is the "
                 'normal answer path — answer from the fact-sheet, which was extracted at '
                 "ingest. If the fact-sheet genuinely doesn't contain the answer, tell Sophie "
                 "it's not on the fact-sheet and offer to re-read the original PDF (that "
                 "full-read path isn't built yet — don't fabricate). DO NOT estimate from "
                 'memory.',
  'input_schema': {'type': 'object',
                   'properties': {'name': {'type': 'string',
                                           'description': 'Document name or type substring.'}},
                   'required': ['name']}}]
