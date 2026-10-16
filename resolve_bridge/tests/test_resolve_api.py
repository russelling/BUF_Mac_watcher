import pytest

from resolve_bridge import resolve_api
from resolve_bridge.cdl import CDLValues
from resolve_bridge.color_plan import ColorPlan


def make_plan(shot_code="301_001_0050", cdl=True, lut=True):
    return ColorPlan(
        shot_code=shot_code,
        camera_family="arri",
        aces_idt="ARRI LogC4",
        cdl_path="/plates/301_001_0050_BG01_v01.cc" if cdl else None,
        cdl_values=CDLValues(slope=(1.05, 1.0, 0.95)) if cdl else None,
        lut_path="/luts/show.cube" if lut else None,
    )


class FakeMediaPoolItem:
    def __init__(self):
        self.properties = {}

    def SetClipProperty(self, name, value):
        self.properties[name] = value
        return True


class FakeNodeGraph:
    def __init__(self, num_nodes=1):
        self._num_nodes = num_nodes
        self.cdl_calls = []
        self.lut_calls = []

    def GetNumNodes(self):
        return self._num_nodes

    def SetCDL(self, cdl_map):
        self.cdl_calls.append(cdl_map)
        return True

    def SetLUT(self, node_index, lut_path):
        self.lut_calls.append((node_index, lut_path))
        return True


class FakeTimelineItem:
    def __init__(self, num_nodes=1, has_node_graph=True):
        self._graph = FakeNodeGraph(num_nodes) if has_node_graph else None
        self.applied_powergrades = []
        self.flat_cdl_calls = []
        self.flat_lut_calls = []

    def GetNodeGraph(self):
        return self._graph

    def ApplyGradeFromDRX(self, path, grade_mode):
        self.applied_powergrades.append((path, grade_mode))
        if self._graph is not None:
            self._graph._num_nodes = max(self._graph._num_nodes, 2)
        return True

    # Flat-API fallback shape, exercised by test_node_target_falls_back_to_timeline_item.
    def SetCDL(self, cdl_map):
        self.flat_cdl_calls.append(cdl_map)
        return True

    def SetLUT(self, node_index, lut_path):
        self.flat_lut_calls.append((node_index, lut_path))
        return True


def test_tag_input_color_space_sets_property():
    item = FakeMediaPoolItem()
    ok = resolve_api.tag_input_color_space(item, "ARRI LogC4", logger=lambda *a: None)
    assert ok is True
    assert item.properties["Input Color Space"] == "ARRI LogC4"


def test_node_target_prefers_node_graph_when_present():
    item = FakeTimelineItem(num_nodes=2, has_node_graph=True)
    target = resolve_api._node_target(item)
    assert target is item._graph


def test_node_target_falls_back_to_timeline_item():
    class NoGraph:
        pass

    item = NoGraph()
    assert resolve_api._node_target(item) is item


def test_apply_color_plan_two_node_default(monkeypatch):
    item = FakeTimelineItem(num_nodes=2)
    mpi = FakeMediaPoolItem()
    plan = make_plan()

    result = resolve_api.apply_color_plan(item, mpi, plan, logger=lambda *a: None)

    assert result.input_color_space_ok is True
    assert result.node_count == 2
    assert result.cdl_ok is True
    assert result.lut_ok is True
    assert item._graph.cdl_calls[0]["NodeIndex"] == "1"
    assert item._graph.lut_calls[0] == (2, "/luts/show.cube")


def test_apply_color_plan_applies_powergrade_first():
    item = FakeTimelineItem(num_nodes=1)
    mpi = FakeMediaPoolItem()
    plan = make_plan()

    result = resolve_api.apply_color_plan(
        item, mpi, plan, powergrade_name="cdl_lut_stack", logger=lambda *a: None
    )

    assert item.applied_powergrades == [("cdl_lut_stack", 0)]
    assert result.node_count == 2
    assert result.cdl_ok is True
    assert result.lut_ok is True


def test_apply_color_plan_single_node_prefers_cdl_over_lut():
    item = FakeTimelineItem(num_nodes=1)
    mpi = FakeMediaPoolItem()
    plan = make_plan(cdl=True, lut=True)

    result = resolve_api.apply_color_plan(item, mpi, plan, logger=lambda *a: None)

    assert result.node_count == 1
    assert result.cdl_ok is True
    assert result.lut_ok is None
    assert any("Show LUT dropped" in w for w in result.warnings)


def test_apply_color_plan_single_node_no_cdl_uses_lut():
    item = FakeTimelineItem(num_nodes=1)
    mpi = FakeMediaPoolItem()
    plan = make_plan(cdl=False, lut=True)

    result = resolve_api.apply_color_plan(item, mpi, plan, logger=lambda *a: None)

    assert result.cdl_ok is None
    assert result.lut_ok is True
    assert item._graph.lut_calls[0] == (1, "/luts/show.cube")


def test_apply_color_plan_single_node_bakes_combined_lut_when_enabled(monkeypatch):
    item = FakeTimelineItem(num_nodes=1)
    mpi = FakeMediaPoolItem()
    plan = make_plan(cdl=True, lut=True)

    monkeypatch.setattr(
        "resolve_bridge.lut_bake.bake_combined_lut", lambda plan, logger=print: "/tmp/combined.cube"
    )

    result = resolve_api.apply_color_plan(
        item, mpi, plan, bake_combined_lut_fallback=True, logger=lambda *a: None
    )

    assert result.strategy == "single-node baked CDL+LUT combined cube"
    assert result.lut_ok is True
    assert item._graph.lut_calls[0] == (1, "/tmp/combined.cube")


def test_apply_color_plan_nothing_to_apply_warns():
    item = FakeTimelineItem(num_nodes=1)
    mpi = FakeMediaPoolItem()
    plan = make_plan(cdl=False, lut=False)

    result = resolve_api.apply_color_plan(item, mpi, plan, logger=lambda *a: None)

    assert result.cdl_ok is None
    assert result.lut_ok is None
    assert any("no CDL and no LUT" in w for w in result.warnings)


def test_configure_aces_color_management_sets_science_mode_first(monkeypatch):
    calls = []

    class FakeProject:
        def SetSetting(self, key, value):
            calls.append((key, value))
            return True

    monkeypatch.setattr(resolve_api.time, "sleep", lambda *_a: None)
    ok = resolve_api.configure_aces_color_management(FakeProject(), logger=lambda *a: None)
    assert ok is True
    assert calls[0][0] == "colorScienceMode"


def test_ensure_bin_reuses_existing_folder():
    class FakeFolder:
        def __init__(self, name):
            self._name = name

        def GetName(self):
            return self._name

    class RootWithSubs(FakeFolder):
        def GetSubFolderList(self):
            return [FakeFolder("dailies_2026_08_31")]

    class FakeMediaPool:
        def __init__(self):
            self.current = None
            self._root = RootWithSubs("root")

        def GetRootFolder(self):
            return self._root

        def SetCurrentFolder(self, folder):
            self.current = folder

        def AddSubFolder(self, root, name):
            raise AssertionError("should not create a new bin when one already exists")

    pool = FakeMediaPool()
    bin_folder = resolve_api.ensure_bin(pool, "dailies_2026_08_31", logger=lambda *a: None)
    assert bin_folder.GetName() == "dailies_2026_08_31"
    assert pool.current is bin_folder


def test_import_media_raises_on_empty_result():
    class FakeMediaPool:
        def ImportMedia(self, paths):
            return []

    with pytest.raises(resolve_api.GradeApplyError):
        resolve_api.import_media(FakeMediaPool(), "/plates/301_001_0050.exr", logger=lambda *a: None)
