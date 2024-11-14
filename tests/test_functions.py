"""Test utility ecl2csv"""

import os
from numpy.ma import allclose, allequal
from shutil import copytree
from time import sleep
from io import BytesIO
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xtgeo import GridProperty, gridproperty_from_file

from fmu.sumo.sim2sumo import grid3d, tables
from fmu.sumo.sim2sumo.common import (
    find_datafiles,
    create_config_dict,
    nodisk_upload,
    Dispatcher,
    find_datefield,
    filter_options,
    get_case_uuid,
)
from fmu.sumo.sim2sumo._special_treatments import (
    _define_submodules,
    convert_to_arrow,
    SUBMODULES,
)
from fmu.sumo.uploader import SumoConnection


from conftest import REEK_REAL0, REEK_REAL1, REEK_DATA_FILE


SLEEP_TIME = 3


def check_sumo(case_uuid, tag_prefix, correct, class_type, sumo):
    # There has been instances when this fails, probably because of
    # some time delay, have introduced a little sleep to make it not fail
    sleep(SLEEP_TIME)
    if not tag_prefix.endswith("*"):
        tag_prefix = tag_prefix + "*"

    path = f"/objects('{case_uuid}')/children"
    query = f"$filter=data.tagname:{tag_prefix}"

    if class_type != "*":
        query += f" AND class:{class_type}"
        check_nr = correct
    else:
        # Plus one because we always upload parameters.txt automatically
        check_nr = correct + 1

    results = sumo.get(path, query).json()

    returned = results["hits"]["total"]["value"]
    assert (
        returned == check_nr
    ), f"Supposed to upload {check_nr}, but actual were {returned}"

    sumo.delete(
        path,
        "$filter=*",
    )
    sleep(SLEEP_TIME)

    sumo.delete(path, query)


def check_expected_exports(expected_exports, shared_grid, prefix):
    parameters = list(shared_grid.glob(f"*--{prefix.lower()}-*.roff"))
    meta = list(shared_grid.glob(f"*--{prefix.lower()}-*.roff.yml"))
    nr_parameter = len(parameters)
    nr_meta = len(meta)
    assert nr_parameter == nr_meta
    assert (
        nr_parameter == expected_exports
    ), f"exported {nr_parameter} params, should be {expected_exports}"
    assert (
        nr_meta == expected_exports
    ), f"exported {nr_meta} metadata objects, should be {expected_exports}"


@pytest.mark.parametrize(
    "submod,options",
    [
        ("summary", {"arrow": True}),
        ("rft", {"md_log_file": "badabing"}),
        ("vfp", {}),
    ],
)
def test_non_standard_filter_options(submod, options):
    returned_options = filter_options(submod, options)
    assert (
        len(returned_options) > 0
    ), f"No options left for {submod}, should still be {options}"


@pytest.mark.parametrize(
    "datestring,expected_result",
    [("bababdbbdd_20240508", "20240508"), ("nodatestring", None)],
)
def test_find_datefield(datestring, expected_result):
    assert find_datefield(datestring) == expected_result


def test_get_case_uuid(case_uuid, scratch_files, monkeypatch):
    real0 = scratch_files[0]
    monkeypatch.chdir(real0)
    uuid = get_case_uuid(real0, parent_level=1)
    assert uuid == case_uuid


@pytest.mark.parametrize(
    "config,nrdatafiles,nrsubmodules",
    [
        ({}, 5, 4),
        (
            {"datafile": [{"3_R001_REEK": ["summary"]}]},
            1,
            2,
        ),
        (
            {"datafile": [{"3_R001_REEK": ["summary", "rft"]}]},
            1,
            3,
        ),
        ({"datafile": ["3_R001_REEK", "OOGRE_PF.in"]}, 2, 4),
        ({"datafile": ["3_R001_REEK"]}, 1, 4),
        ({"datafile": ["3_R001_REEK-1.DATA"]}, 1, 4),
        ({"datafile": ["OOGRE_IX.afi"]}, 1, 4),
        ({"datafile": ["opm/model/OOGRE_OPM.DATA"]}, 1, 4),
        ({"grid3d": True}, 5, 4),
    ],
)
def test_create_config_dict(config, nrdatafiles, nrsubmodules, tmp_path):
    sim2sumo_config = {"sim2sumo": config}
    real1 = tmp_path / "realone"
    copytree(REEK_REAL1, real1)
    os.chdir(real1)
    inputs = create_config_dict(sim2sumo_config)
    assert (
        len(inputs) == nrdatafiles
    ), f"{inputs.keys()} expected to have len {nrdatafiles} datafiles"
    for submod, subdict in inputs.items():
        assert (
            len(subdict) == nrsubmodules
        ), f"{subdict} for {submod} expected to have {nrsubmodules} submodules"


def test_Dispatcher(case_uuid, token, scratch_files, monkeypatch):
    disp = Dispatcher(scratch_files[2], "dev", token=token)
    monkeypatch.chdir(scratch_files[0])
    assert disp._parentid == case_uuid
    assert disp._env == "dev"
    assert isinstance(disp._conn, SumoConnection)
    disp.finish()


def test_xtgeo_2_bytestring(eightfipnum):
    bytestr = grid3d.xtgeo_2_bytestring(eightfipnum)
    assert isinstance(bytestr, bytes)


def test_table_2_bytestring(reekrft):
    bytestr = tables.table_2_bytestring(reekrft)
    assert isinstance(bytestr, bytes)


def test_convert_xtgeo_2_sumo_file(
    eightfipnum, scratch_files, config, case_uuid, sumo, monkeypatch, token
):
    monkeypatch.chdir(scratch_files[0])

    file = grid3d.convert_xtgeo_2_sumo_file(
        scratch_files[1], eightfipnum, "INIT", config
    )
    sumo_conn = SumoConnection(env="dev", token=token)
    nodisk_upload([file], case_uuid, "dev", connection=sumo_conn)
    sleep(SLEEP_TIME)
    obj = get_sumo_object(sumo, case_uuid, "EIGHTCELLS", "FIPNUM")
    prop = gridproperty_from_file(obj)
    assert isinstance(
        prop, GridProperty
    ), f"obj should be xtgeo.GridProperty but is {type(prop)}"
    assert allclose(prop.values, eightfipnum.values)
    assert allequal(prop.values, eightfipnum.values)


def test_convert_table_2_sumo_file(
    reekrft, scratch_files, config, case_uuid, sumo, monkeypatch, token
):
    monkeypatch.chdir(scratch_files[0])

    file = tables.convert_table_2_sumo_file(
        scratch_files[1], reekrft, "rft", config
    )

    sumo_conn = SumoConnection(env="dev", token=token)
    nodisk_upload([file], case_uuid, "dev", connection=sumo_conn)
    sleep(SLEEP_TIME)
    obj = get_sumo_object(sumo, case_uuid, "EIGHTCELLS", "rft")
    table = pq.read_table(obj)
    assert isinstance(
        table, pa.Table
    ), f"obj should be pa.Table but is {type(table)}"
    assert table == reekrft
    check_sumo(case_uuid, "rft", 1, "table", sumo)


def get_sumo_object(sumo, case_uuid, name, tagname):
    path = f"/objects('{case_uuid}')/search"
    results = sumo.get(
        path, f"$query=data.name:{name} AND data.tagname:{tagname}"
    ).json()
    obj_id = results["hits"]["hits"][0]["_id"]
    obj = BytesIO(sumo.get(f"/objects('{obj_id}')/blob").content)
    return obj


def test_generate_grid3d_meta(scratch_files, eightfipnum, config, monkeypatch):
    monkeypatch.chdir(scratch_files[0])
    meta = grid3d.generate_grid3d_meta(
        scratch_files[1], eightfipnum, "INIT", config
    )
    assert isinstance(meta, dict)


def test_upload_init(
    scratch_files, xtgeogrid, config, sumo, token, monkeypatch
):
    monkeypatch.chdir(scratch_files[0])
    disp = Dispatcher(scratch_files[1], "dev", token=token)
    expected_results = 5
    grid3d.upload_init(
        str(scratch_files[1]).replace(".DATA", ".INIT"),
        xtgeogrid,
        config,
        disp,
    )
    uuid = disp.parentid
    disp.finish()
    check_sumo(uuid, "INIT", expected_results, "cpgrid_property", sumo)


def test_upload_restart(
    scratch_files, xtgeogrid, config, sumo, token, monkeypatch
):
    monkeypatch.chdir(scratch_files[0])
    disp = Dispatcher(scratch_files[1], "dev", token=token)

    expected_results = 9
    restart_path = str(scratch_files[1]).replace(".DATA", ".UNRST")
    grid3d.upload_restart(
        restart_path,
        xtgeogrid,
        grid3d.get_timesteps(restart_path, xtgeogrid),
        config,
        disp,
    )
    uuid = disp.parentid
    disp.finish()
    check_sumo(uuid, "UNRST", expected_results, "cpgrid_property", sumo)


def test_upload_tables_from_simulation_run(
    scratch_files, config, sumo, monkeypatch
):
    monkeypatch.chdir(scratch_files[0])

    disp = Dispatcher(scratch_files[1], "dev")
    expected_results = 2
    tables.upload_tables_from_simulation_run(
        REEK_DATA_FILE,
        {"summary": {"arrow": True}, "rft": {"arrow": True}},
        config,
        disp,
    )
    uuid = disp.parentid
    disp.finish()
    check_sumo(uuid, "*", expected_results, "table", sumo)


def test_upload_simulation_run(
    scratch_files, config, sumo, token, monkeypatch
):
    monkeypatch.chdir(scratch_files[0])
    disp = Dispatcher(scratch_files[1], "dev", token=token)

    expected_results = 15
    grid3d.upload_simulation_run(scratch_files[1], config, disp)
    uuid = disp.parentid
    disp.finish()
    check_sumo(uuid, "*", expected_results, "cpgrid*", sumo)


def test_submodules_dict():
    """Test generation of submodule list"""
    sublist, submods = _define_submodules()
    assert isinstance(sublist, tuple)
    assert isinstance(submods, dict)
    for submod_name, submod_dict in submods.items():
        assert isinstance(submod_name, str)
        assert (
            "/" not in submod_name
        ), f"Left part of folder path for {submod_name}"
        assert isinstance(submod_dict, dict), f"{submod_name} has no subdict"
        assert (
            "options" in submod_dict.keys()
        ), f"{submod_name} does not have any options"

        assert isinstance(
            submod_dict["options"], tuple
        ), f"options for {submod_name} not tuple"


@pytest.mark.parametrize(
    "submod",
    (name for name in SUBMODULES if name != "wellcompletiondata"),
)
# Skipping wellcompletion data, since this requires zonemap
def test_get_table(submod):
    """Test fetching of dataframe"""
    frame = tables.get_table(REEK_DATA_FILE, submod, arrow=False)
    assert isinstance(frame, pd.DataFrame), (
        "get_table with arrow=False should return dataframe,"
        f" but returned {type(frame)}"
    )
    frame = tables.get_table(REEK_DATA_FILE, submod, arrow=True)
    assert isinstance(frame, pa.Table), (
        "get_table with arrow=True should return pa.Table,"
        f" but returned {type(frame)}"
    )
    if submod == "summary":
        assert (
            frame.schema.field("FOPT").metadata is not None
        ), "Metdata not carried across for summary"


def test_convert_to_arrow():
    """Test function convert_to_arrow"""
    dframe = pd.DataFrame(
        {
            "DATE": ["2020-01-01", "1984-12-06", "1972-07-16"],
            "NUM": [1, 2, 4],
            "string": ["A", "BE", "SEE"],
        }
    )
    dframe["DATE"] = dframe["DATE"].astype("datetime64[ms]")
    table = convert_to_arrow(dframe)
    assert isinstance(table, pa.Table), "Did not convert to table"


@pytest.mark.parametrize("real,nrdfiles", [(REEK_REAL0, 2), (REEK_REAL1, 5)])
def test_find_datafiles_reek(real, nrdfiles):
    os.chdir(real)
    datafiles = find_datafiles(None)
    expected_tools = ["eclipse", "opm", "ix", "pflotran"]
    assert (
        len(datafiles) == nrdfiles
    ), f"Expected {nrdfiles} datafiles but found {len(datafiles)}"
    for found_path in datafiles:
        parent = found_path.parent.parent.name
        assert parent in expected_tools, f"|{parent}| not in {expected_tools}"
        correct_suff = ".DATA"
        if parent == "ix":
            correct_suff = ".afi"
        if parent == "pflotran":
            correct_suff = ".in"
        assert found_path.suffix == correct_suff
