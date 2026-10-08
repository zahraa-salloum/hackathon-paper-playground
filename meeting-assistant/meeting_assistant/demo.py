"""Original synthetic meetings; no real people, recordings, or private material."""

DEMO_MEETINGS = [
    {"title": "Atlas · Planning", "description": "Synthetic demo · Monday planning session", "segments": [
        {"start": 12, "end": 29, "speaker": "Speaker A", "text": "Atlas is our customer onboarding redesign. We want to reduce the time it takes a new workspace to finish setup."},
        {"start": 45, "end": 63, "speaker": "Speaker B", "text": "For the Atlas pilot, the initial launch date is October 15. We will start with twenty invited workspaces."},
        {"start": 90, "end": 110, "speaker": "Speaker A", "text": "Maya owns the onboarding checklist. Omar owns the CSV importer. Both are required before the pilot can launch."},
        {"start": 155, "end": 178, "speaker": "Speaker C", "text": "We selected SQLite for the local prototype because it works offline and keeps the archive on the user's computer. We are deferring hosted storage."},
        {"start": 220, "end": 241, "speaker": "Speaker B", "text": "Our proposed success target is eighty percent of pilot workspaces completing onboarding in under five minutes. The team has not approved a marketing budget."},
    ]},
    {"title": "Atlas · Design review", "description": "Synthetic demo · Wednesday design review", "segments": [
        {"start": 18, "end": 42, "speaker": "Speaker A", "text": "The onboarding checklist is ready for review. Maya added progress indicators and an option to resume setup later."},
        {"start": 71, "end": 94, "speaker": "Speaker B", "text": "The CSV importer failed on files containing duplicate email addresses. Omar is adding validation and a preview of rejected rows."},
        {"start": 130, "end": 154, "speaker": "Speaker C", "text": "The Atlas pilot launch is moving from October 15 to October 22 because duplicate-email validation in the CSV importer needs another review. This replaces the date from planning."},
        {"start": 180, "end": 203, "speaker": "Speaker A", "text": "We still plan to invite twenty workspaces. The onboarding completion target remains eighty percent in under five minutes."},
        {"start": 242, "end": 261, "speaker": "Speaker B", "text": "No marketing budget was agreed today. We need finance input before setting an amount."},
    ]},
    {"title": "Atlas · Readiness check", "description": "Synthetic demo · Friday readiness check", "segments": [
        {"start": 10, "end": 33, "speaker": "Speaker B", "text": "Omar has completed duplicate-email validation for the CSV importer. We tested the preview of rejected rows with the sample files."},
        {"start": 62, "end": 86, "speaker": "Speaker A", "text": "Maya's onboarding checklist has passed review. Maya will also prepare the short pilot guide before October 20."},
        {"start": 108, "end": 132, "speaker": "Speaker C", "text": "We confirm October 22 as the Atlas pilot launch date. The extra week was needed for the CSV import validation review. Twenty invited workspaces remain in scope."},
        {"start": 162, "end": 184, "speaker": "Speaker B", "text": "For the pilot we will keep the archive local in SQLite. An encrypted cloud backup is a later possibility, and has not been approved."},
        {"start": 221, "end": 246, "speaker": "Speaker A", "text": "The remaining launch action is Maya's pilot guide. Finance still has not supplied a marketing budget, and the meeting contains no approved amount."},
    ]},
]


def seed_demo(store):
    existing = {m["description"]: m for m in store.list_meetings()}
    result = []
    for item in DEMO_MEETINGS:
        if item["description"] in existing:
            result.append(existing[item["description"]])
            continue
        meeting = store.create_meeting(item["title"], consent=True, description=item["description"])
        store.add_segments(meeting["id"], [dict(s, channel="import") for s in item["segments"]])
        store.update_meeting(meeting["id"], status="archived")
        result.append(store.get_meeting(meeting["id"]))
    return {"meetings": result}
