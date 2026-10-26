from database.onboarding import help_is_pending, mark_help_pending, mark_help_shown


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
