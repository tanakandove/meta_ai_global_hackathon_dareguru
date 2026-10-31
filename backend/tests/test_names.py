from app.names import cluster_names


def test_typo_maps_to_majority_spelling_regardless_of_order():
    names = ["MidTown Cmmunity Dashboard", "MidTown Community Dashboard", "MidTown Community Dashboard"]
    m = cluster_names(names)
    assert m["MidTown Cmmunity Dashboard"] == "MidTown Community Dashboard"
    assert set(m.values()) == {"MidTown Community Dashboard"}


def test_distinct_people_stay_distinct():
    m = cluster_names(["Alex", "Marcus", "Priya", "Dana"])
    assert len(set(m.values())) == 4


def test_empty_names_are_skipped():
    assert cluster_names(["", "  ", "Dana"]) == {"Dana": "Dana"}


def test_casing_differences_collapse():
    m = cluster_names(["Dana", "Dana", "dana"])
    assert m["dana"] == "Dana"
