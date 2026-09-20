# AI Automation Architect

**Describe repetitive work in plain English. Get back a validated automation architecture.**

You tell it what you do by hand. It works out the steps, judges which ones a computer
could take over, and, the part that matters, flags where a human has to stay in the
loop and why.

---

## What it looks like

```
$ python scripts/analyse.py "Every morning I go through my emails looking for
  invoices. When I find one I download the PDF attachment, read the total off it,
  and type that into our Google Sheet. Then I message accounting on Slack to say
  it's in. If it's a big one, over five thousand pounds, I check with my manager
  first before I put it through."

Step 1 of 2: mapping the process
  attempt 1: accepted

INVOICE PROCESSING
Process inbound invoice emails every morning by downloading PDFs, logging them in a
Google Sheet, and notifying accounting, with manager sign-off for large invoices.

Trigger: Every morning I go through my emails looking for invoices. (schedule)
Systems: Email, Google Sheet, Slack

  [ ] check_emails  (read)
      Check emails for invoices
      assumed: The user reviews emails in an email client like Gmail or Outlook.
      -> download_pdf

  [ ] download_pdf  (write)
      Download the PDF attachment
      repeats for: each PDF attachment on the email
      -> read_total

  [ ] read_total  (extract)
      Read the total off the PDF
      -> check_amount_size

  [?] check_amount_size  (decision)
      Check if invoice is over five thousand pounds
      -> manager_check     if amount is over five thousand pounds
      -> enter_into_sheet  if amount is five thousand pounds or less

  [ ] manager_check  (judgement)
      Check with manager
      -> enter_into_sheet

  [ ] enter_into_sheet  (write)
      Type total into Google Sheet
      -> notify_accounting

  [ ] notify_accounting  (notify)
      Message accounting on Slack


Step 2 of 2: judging what can be automated
  attempt 1: accepted

Most of the invoice collection, logging, and notification can be fully automated,
leaving only the manager's sign-off on large invoices to be handled by a person.

[ auto  ]  check_emails
            A computer can look for new emails and spot incoming invoices without
            you needing to do it yourself.

[ auto  ]  download_pdf
            An automated rule can grab and save the PDF attachment from the email
            the moment it arrives.

[ auto  ]  read_total
            Optical character recognition can easily read the total amount due
            straight off the invoice PDF.

[ auto  ]  check_amount_size
            A simple arithmetic check can instantly tell whether the invoice total
            is greater than five thousand pounds.

[  you  ]  manager_check
            This is a person checking and signing something off, which is the point
            of the step.
            risks: subjective_judgement

[ auto  ]  enter_into_sheet
            A script can type the invoice total directly into your Google Sheet
            without any manual copying.

[ auto  ]  notify_accounting
            An automated message can be sent straight to accounting on Slack as
            soon as the invoice is logged.

Runs on its own: 6   Needs a guard: 0   Stays with you: 1   Unclear: 0
Start with: check_emails
```

### And when nothing stops for a human

The interesting case. Same tool, a process with no approval step in it:

```
$ python scripts/analyse.py "Every Friday I go through the supplier invoices
  sitting in our shared inbox. I read the amount off each one, pay it straight
  from our business account through the banking portal, mark it as paid in the
  spreadsheet, and then delete the email to keep the inbox tidy."

FRIDAY SUPPLIER INVOICE PAYMENT

  ... process map omitted ...

  Worth asking before building anything:
    - Do invoices need any manager approval before they are paid?
      why: If approval is required, an approval step needs to be added before payment.
      e.g. No, I pay them straight away / Yes, approval is needed for amounts over
           a certain limit / Yes, all invoices need approval

Step 2 of 2: judging what can be automated
  attempt 1: accepted

You can automate the reading, spreadsheet logging, and tidying of invoices, but
actual money transfers will still wait for your final click.

[ auto  ]  open_inbox
[ auto  ]  get_next_invoice
[ auto  ]  read_amount

[ guard ]  pay_invoice
            It can set up the payment for you, but actual money leaving your bank
            account should never happen completely unattended.
            risks: moves_money, irreversible
            -> human_approval
               Added automatically: this step was judged to need a guard, but none
               was specified. Defaulting to asking a person, which is the safe
               assumption.

[ auto  ]  mark_as_paid

[ guard ]  delete_email
            Once the payment is done and logged, a computer can move or delete the
            email to keep things tidy. Flagged automatically from what this step
            does, rather than by judgement.
            risks: irreversible
            -> human_approval

Runs on its own: 4   Needs a guard: 2   Stays with you: 0   Unclear: 0
Start with: pay_invoice
```

Two different mechanisms are visible there. The model worked out by itself that money
leaving an account is different from updating a spreadsheet, and noticed the
description never mentioned an approval step, so it asked.

It did not notice that deleting an email cannot be undone. That one was caught in
code, which is why it says so.

---

## How it works

```
Plain English
      |
      v
 [ MODEL ]   fills in a structured form        <- stage 1: describe
      |
      v
 [ CODE  ]   validates the process graph
      |
      +-- broken? send the faults back and ask again
      |
      v
 [ MODEL ]   judges each step                  <- stage 2: decide
      |
      v
 [ CODE  ]   cross-checks against the graph
      |
      v
 Process map + verdicts + controls
```

Two model calls, not one. Asking a single prompt to both *understand* a process and
*judge* it does each job worse than asking twice.

## The parts worth looking at

**Structural rules the model cannot talk its way out of.**
A step where the process branches must have at least two ways out, each saying what
decides it. Models flatten decisions into straight lines constantly, so it is enforced
in [`process.py`](backend/app/schemas/process.py) rather than requested in a prompt.

**A house rule on risk.**
Any step that moves money, cannot be undone, or carries legal weight can never be
marked fully automatic, regardless of how confident the model was. See
`NEVER_FULLY_AUTOMATIC` in [`assessment.py`](backend/app/schemas/assessment.py).
Guard rails belong in code, not in prompt text.

**Validation that reports every fault at once.**
The validators return a list rather than raising on the first problem, because the
consumer is a repair loop, not a person. A loop that sees one fault per round trip
fixes it, breaks another, and oscillates.

**Repair prompts that say how to fix it.**
An error message reading "that is invalid" produces the same invalid answer again.
Every message names the valid ways out. This was learned the hard way, see the
commit history.

**Risk detection that does not depend on the model noticing.**
The house rule above only fires once a risk is flagged, and that was left entirely
to judgement. It marked "delete the email" as safe to run unattended. Obvious cases
are now caught by looking at what the step says it does, before the model's verdict
is considered. Deliberately crude word matching, because a safety net needs to be
predictable and explainable more than it needs to be clever. It is also careful
about the difference between doing a thing and recording that it happened: "mark as
paid" writes a spreadsheet row, it does not touch a bank account.

**Failing safe rather than closed.**
Smaller models regularly flag a step as needing a guard and then forget to attach
one. Rejecting the whole analysis over that throws away work that was otherwise
correct. Instead a conservative control is inserted, labelled plainly as having been
added automatically. A step that needed an approval and gets one is right. A step
that needed one and gets nothing is how money leaves an account unattended.

**A guard against loops that are not converging.**
If the same complaint comes back twice running, it stops rather than spending more
of a metered quota on an answer that is not changing.

**A provider seam.**
Nothing outside [`llm/gemini.py`](backend/app/llm/gemini.py) knows which model
provider is in use. Swapping it is one file.

**Record and replay.**
Every real call is saved. Two curated cases live in `backend/recordings/`, so
`--replay` reproduces either example above with no network and no API key. The tests
use the same idea with a scripted stand-in.

## Try it

No API key needed. Two recorded cases ship with the repo:

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt

.venv/Scripts/python scripts/analyse.py --replay
.venv/Scripts/python scripts/analyse.py --replay payment-no-approval
```

To run it against your own description you need a free
[Google AI Studio](https://aistudio.google.com) key:

```bash
setx GEMINI_API_KEY "your-key"
# open a new terminal, then:
.venv/Scripts/python scripts/analyse.py "describe something repetitive you do"
```

## Tests

```bash
cd backend
.venv/Scripts/python -m pytest
```

48 tests, none of which call an API. The model is substituted with a scripted
stand-in that returns deliberately broken output, so the repair loop can be tested
precisely and for free.

## Status

Working: the two-stage pipeline, validation, repair, record/replay.

Next: a web front end with the process rendered as a diagram, then export to n8n,
then the time-saved arithmetic.

Built with Python, Pydantic and Gemini. British English throughout, and the example
figures are in pounds, because that is who it is for.

## Licence

MIT. See [LICENSE](LICENSE).
