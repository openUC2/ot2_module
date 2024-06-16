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

def connect_microscope():
    global uc2, state, node_name, ip, port 
    try:
        print(ip)
        uc2 = imc.ImSwitchClient(host=ip, port=port, isHttps=False)
        state = ModuleStatus.IDLE

    except Exception as connection_err:
        state = ModuleStatus.ERROR
        print("Connection error code: " + connection_err)

    else:
        print(str(node_name) + " online")



def execute(protocol_path, payload=None, resource_config=None):
    """
    Compiles the yaml at protocol_path into .py file;
    Transfers and Executes the .py file
    """



def poll_uc2_until_run_completion():
    """Queries the uc2 run state until reported as 'succeeded'"""
    global run_id, state
    print("Polling uc2 run until completion")
    while state != ModuleStatus.IDLE:
        run_status = uc2.get_run(run_id)

        if run_status["data"]["status"] and run_status["data"]["status"] == "succeeded":
            state = ModuleStatus.IDLE
            print("Stopping Poll")

        elif run_status["data"]["status"] and run_status["data"]["status"] == "running":
            state = ModuleStatus.BUSY


@asynccontextmanager
async def lifespan(app: FastAPI):
    global \
        uc2, \
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
    parser.add_argument("--uc2_ip", type=str, help="ip value")
    parser.add_argument("--port", type=int, help="port value")
    args = parser.parse_args()
    node_name = args.alias
    ip = args.uc2_ip
    state = "UNKNOWN"
    temp_dir = Path.home() / ".wei" / ".uc2_temp"
    temp_dir.mkdir(exist_ok=True)
    resources_folder_path = str(temp_dir / node_name / "resources/")
    protocols_folder_path = str(temp_dir / node_name / "protocols/")
    logs_folder_path = str(temp_dir / node_name / "logs/")
    check_resources_folder()
    check_protocols_folder()
    connect_microscope()
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
        model="UC2 Microscope",
        description="Uc2 Microscope that can image a microscpic sample",
        interface="wei_rest_node",
        version=extract_version(Path(__file__).parent.parent / "pyproject.toml"),
        actions=[
            ModuleAction(
                name="set_illumination",
                description="Changes the state of the microscope illumination",
                args=[
                    ModuleActionArg(
                        name="intensity",
                        description="Strength of the illumination.",
                        type="int",
                        required=True,
                        default=0,
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
    global uc2, state
    response = StepResponse()
    if state == ModuleStatus.ERROR:
        # Try to reconnect
        check_resources_folder()
        check_protocols_folder()
        connect_microscope()
        if state == ModuleStatus.ERROR:
            msg = "Can not accept the job! uc2 CONNECTION ERROR"
            response.action_response = StepStatus.FAILED
            response.action_msg = msg
            return response

    while state != ModuleStatus.IDLE:
        #get_logger().warn("Waiting for uc2 to switch IDLE state...")
        time.sleep(0.5)

    state = ModuleStatus.BUSY
    action_command = action_handle
    action_vars = json.loads(action_vars)
    print(f"{action_vars=}")

    print(f"In action callback, command: {action_command}")

    if action_command == "home":
        axis = json.loads(action_vars).get("axis", "X")
        try:
            mResult = uc2.homeAxis(positioner_name=None, axis=axis, is_blocking=True)
            response.action_response = StepStatus.SUCCEEDED
            response.action_msg = "Successfully homed the axis"
            state = ModuleStatus.IDLE
            return response
        except Exception as e:
            response.action_response = StepStatus.FAILED
            response.action_msg = str(e)
            state = ModuleStatus.IDLE
            return response
    elif action_command == "move":
        axis = json.loads(action_vars).get("axis", "X")
        position = json.loads(action_vars).get("position", 0)
        is_absolute = json.loads(action_vars).get("is_absolute", True)
        
        try:
            mResult = uc2.positionersManager.movePositioner(positioner_name=None, axis="X", position=position, is_absolute=is_absolute, is_blocking=True)
            response.action_response = StepStatus.SUCCEEDED
            response.action_msg = "Successfully moved the axis"
            state = ModuleStatus.IDLE
            return response
        except Exception as e:
            response.action_response = StepStatus.FAILED
            response.action_msg = str(e)
            state = ModuleStatus.IDLE
            return response
    
    elif action_command == "illumination":
        intensity = json.loads(action_vars).get("intensity", 0)
        try:
            uc2.client.lasersManager.setLaserActive("LED", True)
            mResult = uc2.lasersManager.setLaserValue("LED", intensity)
            response.action_response = StepStatus.SUCCEEDED
            response.action_msg = "Successfully set the illumination"
            state = ModuleStatus.IDLE
            return response
        except Exception as e:
            response.action_response = StepStatus.FAILED
            response.action_msg = str(e)
            state = ModuleStatus.IDLE
            return response
        
    elif action_command == "scan":
        # client.histoscanManager.startHistoScanTileBasedByParameters(numberTilesX, numberTilesY, stepSizeX, stepSizeY, initPosX, initPosY, nTimes, tPeriod)
        numberTilesX = json.loads(action_vars).get("numberTilesX", 1)
        numberTilesY = json.loads(action_vars).get("numberTilesY", 1)
        stepSizeX = json.loads(action_vars).get("stepSizeX", 1)
        stepSizeY = json.loads(action_vars).get("stepSizeY", 1)
        initPosX = json.loads(action_vars).get("initPosX", 1)
        initPosY = json.loads(action_vars).get("initPosY", 1)
        nTimes = json.loads(action_vars).get("nTimes", 1)
        tPeriod = json.loads(action_vars).get("tPeriod", 1)
        try:
            mResult = uc2.histoscanManager.startHistoScanTileBasedByParameters(numberTilesX, numberTilesY, stepSizeX, stepSizeY, initPosX, initPosY, nTimes, tPeriod)
            response.action_response = StepStatus.SUCCEEDED
            response.action_msg = "Successfully started the scan"
            state = ModuleStatus.IDLE
            return response
        except Exception as e:
            response.action_response = StepStatus.FAILED
            response.action_msg = str(e)
            state = ModuleStatus.IDLE
            return response
    elif action_command == "scan_poslist":
        currentPositions = uc2.positionersManager.getPositionerPositions()[0]
        cX, cY = currentPositions["X"], currentPositions["Y"]
        positionList = []
        nX = json.loads(action_vars).get("nX", 1)
        nY = json.loads(action_vars).get("nY", 1)
        distX = json.loads(action_vars).get("distX", 1)
        distY = json.loads(action_vars).get("distY", 1)
        for ix in range(nX): 
            for iy in range(nY):
                positionList.append((ix*distX+cX,iy*distX+cY,None))
        try:
            mResult = uc2.histoscanManager.startStageScanningPositionlistbased(positionList, nTimes=1, tPeriod=1)
            response.action_response = StepStatus.SUCCEEDED
            response.action_msg = "Successfully started the scan"
            state = ModuleStatus.IDLE
            return response
        except Exception as e:
            response.action_response = StepStatus.FAILED
            response.action_msg = str(e)
            state = ModuleStatus.IDLE
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
    parser.add_argument("--alias", type=str, help="Name of the Node", default="uc2")
    parser.add_argument("--host", type=str, help="Host for rest", default="0.0.0.0")
    parser.add_argument("--uc2_ip", type=str, help="ip value")
    parser.add_argument("--port", type=int, help="port value", default=8001)
    args = parser.parse_args()
    node_name = args.alias
    ip = args.uc2_ip
    uvicorn.run(
        "uc2_rest_node:app",
        host=args.host,
        port=args.port,
        reload=False,
        ws_max_size=100000000000000000000000000000000000000,
    )
