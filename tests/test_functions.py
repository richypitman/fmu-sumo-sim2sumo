"""Test utility ecl2csv"""

import logging
import os
from pathlib import Path
from numpy.ma import allclose, allequal
from subprocess import PIPE, Popen
from time import sleep
from io import BytesIO
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from xtgeo import Grid, GridProperty, gridproperty_from_file

from fmu.sumo.sim2sumo.common import (
    yaml_load,
    nodisk_upload,
    Dispatcher,
    find_datefield,
)
from fmu.sumo.sim2sumo import grid3d, main, tables
from fmu.sumo.sim2sumo._special_treatments import (
    _define_submodules,
    convert_to_arrow,
    SUBMODULES,
)
from fmu.sumo.sim2sumo.common import fix_suffix, get_case_uuid
from fmu.sumo.uploader import SumoConnection

REEK_ROOT = Path(__file__).parent / "data/reek"
REAL_PATH = "realization-0/iter-0/"
REEK_REAL0 = REEK_ROOT / "realization-0/iter-0/"
REEK_REAL1 = REEK_ROOT / "realization-1/iter-0/"
REEK_BASE = "2_R001_REEK"
REEK_ECL_MODEL = REEK_REAL0 / "eclipse/model/"
REEK_DATA_FILE = REEK_ECL_MODEL / f"{REEK_BASE}-0.DATA"
CONFIG_OUT_PATH = REEK_REAL0 / "fmuconfig/output/"
CONFIG_PATH = CONFIG_OUT_PATH / "global_variables.yml"


LOGGER = logging.getLogger(__file__)
SLEEP_TIME = 3


def check_sumo(case_uuid, tag_prefix, correct, class_type, sumo):
    # There has been instances when this fails, probably because of
    # some time delay, have introduced a little sleep to get it to be quicker
    sleep(SLEEP_TIME)
    if tag_prefix == "*":
        search_pattern = "*"
    elif tag_prefix.endswith("*"):
        search_pattern = tag_prefix
    else:
        search_pattern = tag_prefix + "*"

    path = f"/objects('{case_uuid}')/children"
    query = f"$filter=data.tagname:{search_pattern}"

    if class_type != "*":
        query += f" AND class:{class_type}"
        check_nr = correct
    else:
        # The plus one is because we are always uploading the parameters.txt automatically
        check_nr = correct + 1
    print(query)

    results = sumo.get(path, query).json()

    LOGGER.debug(results["hits"])
    returned = results["hits"]["total"]["value"]
    LOGGER.debug("This is returned %s", returned)
    assert (
        returned == check_nr
    ), f"Supposed to upload {correct}, but actual were {returned}"

    print(f"**************\nFound {correct} {class_type} objects")

    sumo.delete(
        path,
        "$filter=*",
    )
    sleep(SLEEP_TIME)

    sumo.delete(path, query)


def write_ert_config_and_run(runpath):
    ert_config_path = "sim2sumo.ert"
    encoding = "utf-8"
    ert_full_config_path = runpath / ert_config_path
    print(f"Running with path {ert_full_config_path}")
    with open(ert_full_config_path, "w", encoding=encoding) as stream:

        stream.write(
            f"DEFINE <SUMO_ENV> dev\nNUM_REALIZATIONS 1\nMAX_SUBMIT 1\nRUNPATH {runpath}\nFORWARD_MODEL SIM2SUMO"
        )
    process = Popen(
        ["ert", "test_run", str(ert_full_config_path)],
        stdout=PIPE,
        stderr=PIPE,
    )
    stdout, stderr = process.communicate()
    if stdout:
        print(stdout.decode(encoding))
    if stderr:
        print(stderr.decode(encoding))
    assert Path(
        runpath / "OK"
    ).is_file(), f"running {ert_full_config_path}, ended with errors"


def _assert_right_len(checks, key, to_messure, name):
    """Assert length when reading config

    Args:
        checks (dict): the answers
        key (str): the answer to check
        to_messure (list): the generated answer
        name (str): name of the file to check against
    """
    # Helper for test_read_config
    right_len = checks[key]
    actual_len = len(to_messure)
    assert (
        actual_len == right_len
    ), f"For {name}-{key} actual length is {actual_len}, but should be {right_len}"


def check_expected_exports(expected_exports, shared_grid, prefix):
    print("Looking in ", shared_grid)
    parameters = list(shared_grid.glob(f"*--{prefix.lower()}-*.roff"))
    print(parameters)
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


@pytest.mark.parametrize("datestring", ["bababdbbdd_20240508", "nodatestring"])
def test_find_datefield(datestring):

    results = find_datefield(datestring)
    print(results)


def test_fix_suffix():

    test_path = "simulator.banana"
    corrected_path = fix_suffix(test_path)
    assert corrected_path.endswith(".DATA"), f"Didn't correct {corrected_path}"


def test_get_case_uuid(case_uuid, scratch_files):

    real0 = scratch_files[0]

    uuid = get_case_uuid(real0, parent_level=1)

    assert uuid == case_uuid


def test_Dispatcher(case_uuid, token, scratch_files):
    disp = Dispatcher(scratch_files[2], "dev", token=token)
    assert disp._parentid == case_uuid
    assert disp._env == "dev"
    assert isinstance(disp._conn, SumoConnection)
    disp.finish()


def test_xtgeo_2_bytes(eightfipnum):

    bytestring = grid3d.xtgeo_2_bytes(eightfipnum)
    assert isinstance(bytestring, bytes)


def test_xtgeo_2_bytestring(eightfipnum):
    bytestr = grid3d.xtgeo_2_bytestring(eightfipnum)
    assert isinstance(bytestr, bytes)


def test_convert_xtgeo_2_sumo_file(
    eightfipnum, scratch_files, config, case_uuid, sumo
):

    file = grid3d.convert_xtgeo_2_sumo_file(
        scratch_files[1], eightfipnum, "INIT", config
    )
    print(case_uuid)
    print(file.metadata)
    print(file.byte_string)
    nodisk_upload([file], case_uuid, "dev")
    obj = get_sumo_object(sumo, case_uuid, "EIGHTCELLS", "FIPNUM")
    prop = gridproperty_from_file(obj)
    assert isinstance(
        prop, GridProperty
    ), f"obj should be xtgeo.GridProperty but is {type(prop)}"
    assert allclose(prop.values, eightfipnum.values)
    assert allequal(prop.values, eightfipnum.values)


def test_convert_table_2_sumo_file(
    reekrft,
    scratch_files,
    config,
    case_uuid,
    sumo,
):

    file = tables.convert_table_2_sumo_file(
        scratch_files[1], reekrft, "rft", config
    )

    print(file.metadata)
    print(file.byte_string)
    nodisk_upload([file], case_uuid, "dev")
    obj = get_sumo_object(sumo, case_uuid, "EIGHTCELLS", "rft")
    table = pq.read_table(obj)
    assert isinstance(
        table, pa.Table
    ), f"obj should be pa.Table but is {type(table)}"
    assert table == reekrft
    check_sumo(case_uuid, "rft", 1, "table", sumo)


def get_sumo_object(sumo, case_uuid, name, tagname):
    print("Fetching object with name, and tag", name, tagname)
    sleep(SLEEP_TIME)
    path = f"/objects('{case_uuid}')/search"
    results = sumo.get(
        path, f"$query=data.name:{name} AND data.tagname:{tagname}"
    ).json()
    print(results)
    obj_id = results["hits"]["hits"][0]["_id"]
    obj = BytesIO(sumo.get(f"/objects('{obj_id}')/blob").content)
    print(type(obj))
    return obj


def test_generate_grid3d_meta(eightcells_datafile, eightfipnum, config):
    meta = grid3d.generate_grid3d_meta(
        eightcells_datafile, eightfipnum, "INIT", config, "property"
    )
    assert isinstance(meta, dict)


def test_upload_init(scratch_files, xtgeogrid, config, sumo, token):
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


def test_upload_restart(scratch_files, xtgeogrid, config, sumo, token):
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


def test_upload_tables_from_simulation_run(scratch_files, config, sumo):
    disp = Dispatcher(scratch_files[1], "dev")
    expected_results = 2
    tables.upload_tables_from_simulation_run(
        REEK_DATA_FILE, ["summary", "rft"], [], config, disp
    )
    uuid = disp.parentid
    disp.finish()
    check_sumo(uuid, "*", expected_results, "table", sumo)


def test_upload_simulation_run(scratch_files, config, sumo, token):
    disp = Dispatcher(scratch_files[1], "dev", token=token)

    expected_results = 15
    grid3d.upload_simulation_run(scratch_files[1], config, disp)
    uuid = disp.parentid
    disp.finish()
    check_sumo(uuid, "*", expected_results, "cpgrid*", sumo)


def test_submodules_dict():
    """Test generation of submodule list"""
    sublist, submods = _define_submodules()
    LOGGER.info(submods)
    assert isinstance(sublist, tuple)
    assert isinstance(submods, dict)
    for submod_name, submod_dict in submods.items():
        LOGGER.info(submod_name)
        LOGGER.info(submod_dict)
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
# Skipping wellcompletion data, since this needs zonemap, which none of the others do
def test_get_table(submod):
    """Test fetching of dataframe"""
    extras = {}
    if submod == "wellcompletiondata":
        extras["zonemap"] = "data/reek/zones.lyr"
    frame = tables.get_table(REEK_DATA_FILE, submod)
    assert isinstance(
        frame, pa.Table
    ), f"Call for get_dataframe should produce dataframe, but produces {type(frame)}"
    frame = tables.get_table(REEK_DATA_FILE, submod, arrow=True)
    assert isinstance(
        frame, pa.Table
    ), f"Call for get_dataframe with arrow=True should produce pa.Table, but produces {type(frame)}"
    if submod == "summary":
        assert (
            frame.schema.field("FOPT").metadata is not None
        ), "Metdata not carried across for summary"


# Extra checks to be used with parametrize below
CHECK_DICT = {
    "global_variables_w_eclpath.yml": {
        "nrdatafile": 1,
        "nrsubmods": 3,
        "nroptions": 1,
        "arrow": True,
    },
    "global_variables_w_eclpath_and_extras.yml": {
        "nrdatafile": 1,
        "nrsubmods": 3,
        "nroptions": 4,
        "arrow": False,
    },
    "global_variables.yml": {
        "nrdatafile": 2,
        "nrsubmods": 3,
        "nroptions": 1,
        "arrow": True,
    },
}


@pytest.mark.parametrize("config_path", CONFIG_OUT_PATH.glob("*.yml"))
def test_read_config(config_path):
    """Test reading of config file via read_config function"""
    os.chdir(REEK_REAL0)
    LOGGER.info(config_path)
    config = yaml_load(config_path)
    assert isinstance(config, (dict, bool))
    sim2sumoconfig = main.read_config(config)
    dfiles = sim2sumoconfig["datafiles"]
    submods = sim2sumoconfig["submods"]
    options = sim2sumoconfig["options"]
    name = config_path.name
    checks = CHECK_DICT[name]
    LOGGER.info("Config keys: %s\n", config.keys())
    LOGGER.info("Datafiles: %s\n", dfiles)
    LOGGER.info("Submods: %s\n", submods)
    LOGGER.info("Options: %s\n", options)
    _assert_right_len(checks, "nrdatafile", dfiles, name)
    _assert_right_len(checks, "nrsubmods", submods, name)
    _assert_right_len(checks, "nroptions", options, name)

    assert (
        options["arrow"] == checks["arrow"]
    ), f"Wrong choice for arrow for {name}"


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


def test_get_xtgeo_egrid(eightcells_datafile):
    egrid = grid3d.get_xtgeo_egrid(eightcells_datafile)
    assert isinstance(egrid, Grid), f"Expected xtgeo.Grid, got {type(egrid)}"


def test_sim2sumo_with_ert(scratch_files, case_uuid, sumo):
    real0 = scratch_files[0]
    write_ert_config_and_run(real0)
    expected_exports = 88
    path = f"/objects('{case_uuid}')/children"
    results = sumo.get(path).json()
    returned = results["hits"]["total"]["value"]
    LOGGER.debug("This is returned %s", returned)
    assert (
        returned == expected_exports
    ), f"Supposed to upload {expected_exports}, but actual were {returned}"


@pytest.mark.parametrize("real,nrdfiles", [(REEK_REAL0, 2), (REEK_REAL1, 5)])
def test_find_datafiles_reek(real, nrdfiles):

    os.chdir(real)
    datafiles = main.find_datafiles(None, {})
    expected_tools = ["eclipse", "opm", "ix", "pflotran"]
    assert (
        len(datafiles) == nrdfiles
    ), f"Haven't found correct nr of datafiles {nrdfiles} files but {len(datafiles)} ({datafiles})"
    for datafile in datafiles:
        found_path = datafile
        parent = found_path.parent.parent.name
        assert parent in expected_tools, f"|{parent}| not in {expected_tools}"
        correct_suff = ".DATA"
        if parent == "ix":
            correct_suff = ".afi"
        if parent == "pflotran":
            correct_suff = ".in"
        assert found_path.suffix == correct_suff
