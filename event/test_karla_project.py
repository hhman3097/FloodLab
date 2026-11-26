import pytest

from ems_project import EventTools


def test_karla_inventory_lists_two_methods_and_dates(tmp_path):
    inv = EventTools(tmp_path).call("karla_inventory", {})
    options = inv["observation_options"]
    assert options["sar"]["orbit"] == 7
    assert options["sar"]["dates"][-1] == "2023-12-18"
    assert options["optical"]["dates"][-1] == "2023-12-19"
    assert {row["method"] for row in options.values()} == {"sar_change", "optical_mndwi"}


@pytest.mark.parametrize("tool,args", [
    ("karla_segment", {"download_id":"../another", "algorithm":"optical_mndwi", "reason":"Cannot use a foreign raster."}),
    ("karla_compare", {"sar_id":"unowned", "optical_id":"unowned"}),
    ("karla_exposure", {"comparison_id":"unowned", "scope":"north"}),
])
def test_karla_rejects_unknown_workspace_artifacts(tmp_path,tool,args):
    event = EventTools(tmp_path)
    with pytest.raises(RuntimeError,match="this conversation"):
        event.call(tool,args)
    assert not (tmp_path/"outputs").exists()
