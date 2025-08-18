from main_SuperOpsTickets_import import get_assigned_tech_and_user


def test_earliest_tech_reply_is_chosen():
    conversations = [
        {
            "type": "CUSTOMER_REPLY",
            "time": 0,
            "user": {"name": "Customer"},
            "toUsers": [{"user": "Tech"}],
        },
        {
            "type": "TECH_REPLY",
            "time": 2,
            "user": {"name": "Tech2"},
            "toUsers": [{"user": "User2"}],
        },
        {
            "type": "TECH_REPLY",
            "time": 1,
            "user": {"name": "Tech1"},
            "toUsers": [{"user": "User1"}],
        },
    ]

    tech, to_users = get_assigned_tech_and_user(conversations)

    assert tech["name"] == "Tech1"
    assert to_users == [{"user": "User1"}]

