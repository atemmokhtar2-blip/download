from database.onboarding import (
    help_is_pending,
    mark_help_pending,
    mark_help_shown,
    next_start_greeting_index,
    get_due_reengagement_users,
    mark_reengagement_stage,
    record_user_activity,
)


def test_help_invitation_is_persistent_and_one_time(isolated_db):
    user_id = 912345678
    assert not help_is_pending(user_id)

    mark_help_pending(user_id)
    assert help_is_pending(user_id)

    mark_help_shown(user_id)
    assert not help_is_pending(user_id)

    # A duplicate /start or duplicate registration must not reset the shown flag.
    mark_help_pending(user_id)
    assert not help_is_pending(user_id)



def test_start_greetings_rotate_per_user_and_persist(isolated_db):
    user_a = 912345679
    user_b = 912345680
    total = 20

    first_cycle = [next_start_greeting_index(user_a, total) for _ in range(total)]
    assert first_cycle == list(range(total))
    # The next /start wraps to the beginning, but never repeats the prior index.
    assert next_start_greeting_index(user_a, total) == 0
    assert next_start_greeting_index(user_a, total) == 1

    # Each user has an independent rotation sequence.
    assert next_start_greeting_index(user_b, total) == 0



def test_inactivity_reminders_follow_3_7_14_day_stages_and_reset(isolated_db):
    from database.db import db_cursor
    from database.users import create_user

    user_id = 912345681
    create_user(user_id, "inactive_test", "Inactive", "", "ar")

    # Eight days inactive with stage one already sent -> next eligible stage is 7 days.
    with db_cursor() as cursor:
        cursor.execute(
            "UPDATE users SET last_seen = datetime('now', '-8 days') WHERE user_id = ?",
            (user_id,),
        )
        cursor.execute(
            "INSERT INTO user_reengagement (user_id, stage) VALUES (?, 1)",
            (user_id,),
        )

    due = [item for item in get_due_reengagement_users() if item["user_id"] == user_id]
    assert len(due) == 1
    assert due[0]["due_stage"] == 2

    # Activity must refresh last_seen and reset the reminder cycle.
    record_user_activity(user_id)
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT stage FROM user_reengagement WHERE user_id = ?",
            (user_id,),
        )
        assert cursor.fetchone()["stage"] == 0
    assert not [item for item in get_due_reengagement_users() if item["user_id"] == user_id]

    # A long outage/inactivity catches up with one final-stage reminder, not three messages.
    with db_cursor() as cursor:
        cursor.execute(
            "UPDATE users SET last_seen = datetime('now', '-20 days') WHERE user_id = ?",
            (user_id,),
        )
    due = [item for item in get_due_reengagement_users() if item["user_id"] == user_id]
    assert len(due) == 1
    assert due[0]["due_stage"] == 3

    mark_reengagement_stage(user_id, 3)
    assert not [item for item in get_due_reengagement_users() if item["user_id"] == user_id]
