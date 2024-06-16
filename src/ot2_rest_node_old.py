#! /usr/bin/env python3
"""
The UC2 microscope server takes incoming WEI flow requests from the experiment application
"""
import ast
import glob
import json
import os
import time
import traceback
from argparse import ArgumentParser
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.error import HTTPError, URLError

import requests
import yaml
from fastapi import FastAPI, UploadFile
from fastapi.responses import JSONResponse
from urllib3.exceptions import ConnectTimeoutError
from wei.core.data_classes import (
    ModuleAbout,
    ModuleAction,
    ModuleActionArg,
    ModuleActionFile,
    ModuleStatus,
    StepFileResponse,
    StepResponse,
    StepStatus,
)
from wei.helpers import extract_version

import imswitchclient.ImSwitchClient as imc 
import numpy as np
import matplotlib.pyplot as plt
import cv2
import time


''' 
parameters of the local microscope
'''
workcell = None
global state
local_ip = "0.0.0.0"
local_port = "8001"

global uc2
resources_folder_path = ""
protocols_folder_path = ""
logs_folder_path = ""
node_name = ""
resource_file_path = ""
ip = ""

def check_protocols_folder():
    """
    Description: Checks if the protocols folder path exists. Creates the resource folder path if it doesn't already exist
    """
    global protocols_folder_path
    isPathExist = os.path.exists(protocols_folder_path)
    if not isPathExist:
        os.makedirs(protocols_folder_path)


def check_resources_folder():
    """
    Description: Checks if the resources folder path exists. Creates the resource folder path if it doesn't already exist
    """
    global resources_folder_path
    isPathExist = os.path.exists(resources_folder_path)
    if not isPathExist:
        os.makedirs(resources_folder_path)
    if not isPathExist:
        os.makedirs(protocols_folder_path)
        print("Creating: " + protocols_folder_path)


def connect_robot():
    global ot2, state, node_name, ip
    try:
        print(ip)
        ot2 = OT2_Driver(OT2_Config(ip=ip))
        state = ModuleStatus.IDLE

    except ConnectTimeoutError as connection_err:
        state = ModuleStatus.ERROR
        print("Connection error code: " + connection_err)

    except HTTPError as http_error:
        print("HTTP error code: " + http_error)

    except URLError as url_err:
        print("Url error code: " + url_err)

    except requests.exceptions.ConnectionError as conn_err:
        print("Connection error code: " + str(conn_err))

    except Exception as error_msg:
        state = ModuleStatus.ERROR
        print("-------" + str(error_msg) + " -------")

    else:
        print(str(node_name) + " online")


def save_config_files(protocol: str, resource_config=None):
    """
    Saves protocol string to a local yaml or python file

    Parameters:
    -----------
    protocol: str
        String contents of yaml or python protocol file

    Returns
    -----------
    config_file_path: str
        Absolute path to generated yaml or python file
    """
    global node_name, resource_file_path
    config_dir_path = Path.home().resolve() / protocols_folder_path
    config_dir_path.mkdir(exist_ok=True, parents=True)

    resource_dir_path = Path.home().resolve() / resources_folder_path
    resource_dir_path.mkdir(exist_ok=True, parents=True)

    time_str = datetime.now().strftime("%Y%m%d-%H%m%s")

    config_file_path = None

    try:  # *Check if the protocol is a python file
        ast.parse(protocol)
        config_file_path = config_dir_path / f"protocol-{time_str}.py"
        with open(config_file_path, "w", encoding="utf-8") as pc_file:
            pc_file.write(protocol)
    except SyntaxError:
        try:  # *Check if the protocol is a yaml file
            config_file_path = config_dir_path / f"protocol-{time_str}.yaml"
            with open(config_file_path, "w", encoding="utf-8") as pc_file:
                yaml.dump(
                    yaml.safe_load(protocol),
                    pc_file,
                    indent=4,
                    sort_keys=False,
                    encoding="utf-8",
                )
        except yaml.YAMLError as e:
            raise ValueError("Protocol is neither a python file nor a yaml file") from e

    if resource_config:
        resource_file_path = resource_dir_path / f"resource-{node_name}-{time_str}.json"
        with open(resource_config) as resource_content:
            content = json.load(resource_content)
        json.dump(content, resource_file_path.open("w"))
        return config_file_path, resource_file_path
    else:
        return config_file_path, None


def execute(protocol_path, payload=None, resource_config=None):
    """
    Compiles the yaml at protocol_path into .py file;
    Transfers and Executes the .py file

    Parameters:
    -----------
    protocol_path: str
        absolute path to the yaml protocol

    Returns
    -----------
    response: bool
        If the ot2 execution was successful
    """

    global run_id, node_name, protocols_folder_path, resources_folder_path
    if Path(protocol_path).suffix == ".yaml":
        print("YAML")
        (
            protocol_file_path,
            resource_file_path,
        ) = ot2.compile_protocol(
            protocol_path,
            payload=payload,
            resource_file=resource_config,
            resource_path=resources_folder_path,
            protocol_out_path=protocols_folder_path,
        )
        protocol_file_path = Path(protocol_file_path)
    else:
        print("PYTHON")
        protocol_file_path = Path(protocol_path)
    print(f"{protocol_file_path.resolve()=}")
    try:
        protocol_id, run_id = ot2.transfer(protocol_file_path)
        print("OT2 " + node_name + " protocol transfer successful")
        resp = ot2.execute(run_id)

        if resp["data"]["status"] == "succeeded":
            # poll_OT2_until_run_completion()
            print("OT2 " + node_name + " succeeded in executing a protocol")
            response_msg = "OT2 " + node_name + " successfully IDLE running a protocol"
            return True, response_msg, run_id

        else:
            print("OT2 " + node_name + " failed in executing a protocol")
            print(resp["data"])
            response_msg = (
                "OT2 " + node_name + " failed running a protocol\n" + str(resp["data"])
            )
            return False, response_msg, run_id
    except Exception as err:
        if "no route to host" in str(err.args).lower():
            response_msg = "No route to host error. Ensure that this container \
            has network access to the robot and that the environment \
            variable, robot_ip, matches the ip of the connected robot \
            on the shared LAN."
            print(response_msg)

        response_msg = f"Error: {traceback.format_exc()}"
        print(response_msg)
        return False, response_msg, None


def poll_OT2_until_run_completion():
    """Queries the OT2 run state until reported as 'succeeded'"""
    global run_id, state
    print("Polling OT2 run until completion")
    while state != ModuleStatus.IDLE:
        run_status = ot2.get_run(run_id)

        if run_status["data"]["status"] and run_status["data"]["status"] == "succeeded":
            state = ModuleStatus.IDLE
            print("Stopping Poll")

        elif run_status["data"]["status"] and run_status["data"]["status"] == "running":
            state = ModuleStatus.BUSY


@asynccontextmanager
async def lifespan(app: FastAPI):
    global \
        ot2, \
        state, \
        node_name, \
        resources_folder_path, \
        protocols_folder_path, \
        logs_folder_path, \
        ip
    """Initial run function for the app, parses the workcell argument
            Parameters
            ----------
            app : FastApi
            The REST API app being initialized

            Returns
            -------
            None"""
    parser = ArgumentParser()
    parser.add_argument("--alias", type=str, help="Name of the Node")
    parser.add_argument("--host", type=str, help="Host for rest")
    parser.add_argument("--ot2_ip", type=str, help="ip value")
    parser.add_argument("--port", type=int, help="port value")
    args = parser.parse_args()
    node_name = args.alias
    ip = args.ot2_ip
    state = "UNKNOWN"
    temp_dir = Path.home() / ".wei" / ".ot2_temp"
    temp_dir.mkdir(exist_ok=True)
    resources_folder_path = str(temp_dir / node_name / "resources/")
    protocols_folder_path = str(temp_dir / node_name / "protocols/")
    logs_folder_path = str(temp_dir / node_name / "logs/")
    check_resources_folder()
    check_protocols_folder()
    connect_robot()
    yield
    pass


app = FastAPI(
    lifespan=lifespan,
)


@app.get("/state")
def get_state():
    global state
    return JSONResponse(content={"State": state})


@app.get("/about")
async def about() -> ModuleAbout:
    global node_name
    return ModuleAbout(
        name=node_name,
        model="Opentrons OT2",
        description="Opentrons OT2 Liquidhandling robot",
        interface="wei_rest_node",
        version=extract_version(Path(__file__).parent.parent / "pyproject.toml"),
        actions=[
            ModuleAction(
                name="run_protocol",
                description="Runs an Opentrons protocol (either python or YAML) on the connected OT2.",
                args=[
                    ModuleActionArg(
                        name="resource_path",
                        description="Not currently implemented.",
                        type="[str, Path]",
                        required=False,
                        default=None,
                    ),
                    ModuleActionArg(
                        name="use_existing_resources",
                        description="Whether or not to use the existing resources file (essentially, whether we've restocked or not).",
                        type="bool",
                        required=False,
                        default=False,
                    ),
                ],
                files=[
                    ModuleActionFile(
                        name="protocol",
                        required="True",
                        description="A protocol file to be run (either python or YAML) on the connected OT2.",
                    ),
                ],
            ),
        ],
        resource_pools=[],
    )


@app.get("/resources")
async def resources():
    global resource_file_path
    resource_info = ""
    if not (resource_file_path == ""):
        with open(resource_file_path) as f:
            resource_info = f.read()
    return JSONResponse(content={"State": resource_info})


@app.post("/action")
def do_action(action_handle: str, action_vars: str, files: List[UploadFile] = []):
    """
    Runs an action on the module

    Parameters
    ----------
    action_handle : str
       The name of the action to be performed
    action_vars : str
        Any arguments necessary to run that action.
        This should be a JSON object encoded as a string.
    files: List[UploadFile] = []
        Any files necessary to run the action defined by action_handle.

    Returns
    -------
    response: StepResponse
       A response object containing the result of the action
    """
    global ot2, state
    response = StepResponse()
    if state == ModuleStatus.ERROR:
        # Try to reconnect
        check_resources_folder()
        check_protocols_folder()
        connect_robot()
        if state == ModuleStatus.ERROR:
            msg = "Can not accept the job! OT2 CONNECTION ERROR"
            response.action_response = StepStatus.FAILED
            response.action_msg = msg
            return response

    while state != ModuleStatus.IDLE:
        #   get_logger().warn("Waiting for OT2 to switch IDLE state...")
        time.sleep(0.5)

    state = ModuleStatus.BUSY
    action_command = action_handle
    action_vars = json.loads(action_vars)
    print(f"{action_vars=}")

    print(f"In action callback, command: {action_command}")

    if "run_protocol" == action_command:
        resource_config = action_vars.get(
            "resource_path", None
        )  # TODO: This will be enabled in the future
        resource_file_flag = action_vars.get(
            "use_existing_resources", "False"
        )  # Returns True to use a resource file or False to not use a resource file.

        if resource_file_flag:
            try:
                list_of_files = glob.glob(
                    resources_folder_path + "*.json"
                )  # Get list of files
                if len(list_of_files) > 0:
                    resource_config = max(
                        list_of_files, key=os.path.getctime
                    )  # Finding the latest added file
                    print("Using the resource file: " + resource_config)

            except Exception as er:
                print(er)

        # * Get the protocol file
        try:
            protocol = next(file for file in files if file.filename == "protocol")
            protocol = protocol.file.read().decode("utf-8")
        except StopIteration:
            protocol = None

        print(f"{protocol=}")

        if protocol:
            config_file_path, resource_config_path = save_config_files(
                protocol, resource_config
            )
            payload = deepcopy(action_vars)

            print(f"ot2 {payload=}")
            print(f"config_file_path: {config_file_path}")

            response_flag, response_msg, run_id = execute(
                config_file_path, payload, resource_config_path
            )

            if response_flag:
                state = ModuleStatus.IDLE
                Path(logs_folder_path).mkdir(parents=True, exist_ok=True)
                with open(Path(logs_folder_path) / f"{run_id}.json", "w") as f:
                    json.dump(ot2.get_run_log(run_id), f, indent=2)
                    print("Finished Action: " + action_handle)
                    return StepFileResponse(
                        action_response=StepStatus.SUCCEEDED,
                        action_log=response_msg,
                        path=f.name,
                    )
                # if resource_config_path:
                #   response.resources = str(resource_config_path)

            elif not response_flag:
                state = ModuleStatus.ERROR
                response.action_response = StepStatus.FAILED
                response.action_msg = response_msg
                # if resource_config_path:
                #   response.resources = str(resource_config_path)

            print("Finished Action: " + action_handle)
            return response

        else:
            response["action_msg"] = "Required 'protocol' file was not provided"
            response.action_response = StepStatus.FAILED
            print(response.action_msg)
            state = ModuleStatus.ERROR

            return response
    else:
        msg = "UNKNOWN ACTION REQUEST! Available actions: run_protocol"
        response.action_response = StepStatus.FAILED
        response.action_msg = msg
        print("Error: " + msg)
        state = ModuleStatus.IDLE

        return response


if __name__ == "__main__":
    import uvicorn

    parser = ArgumentParser()
    parser.add_argument("--alias", type=str, help="Name of the Node", default="ot2")
    parser.add_argument("--host", type=str, help="Host for rest", default="0.0.0.0")
    parser.add_argument("--ot2_ip", type=str, help="ip value")
    parser.add_argument("--port", type=int, help="port value", default=2005)
    args = parser.parse_args()
    node_name = args.alias
    ip = args.ot2_ip
    uvicorn.run(
        "ot2_rest_node:app",
        host=args.host,
        port=args.port,
        reload=False,
        ws_max_size=100000000000000000000000000000000000000,
    )
