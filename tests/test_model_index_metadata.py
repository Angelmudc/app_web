from config_app import db
import models  # noqa: F401 - register all ORM tables in metadata


def test_bot_candidate_phone_index_is_declared_once_with_unchanged_shape():
    table = db.metadata.tables["bot_candidate_intakes"]
    indexes = [index for index in table.indexes if index.name == "ix_bot_candidate_intakes_phone"]

    assert len(indexes) == 1
    assert [column.name for column in indexes[0].columns] == ["phone"]
