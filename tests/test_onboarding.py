from database.onboarding import (
    help_is_pending,
    mark_help_pending,
    mark_help_shown,
    next_start_greeting_index,
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
