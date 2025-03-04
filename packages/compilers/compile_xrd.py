# -*- coding: utf-8 -*-
"""
to complete

@author: williamrigaut
"""
import h5py
import os
import numpy as np
import fabio
from packages.compilers.compile_hdf5 import (
    convertFloat,
    is_outside_wafer,
    create_multiple_groups,
)


def get_scan_numbers(filepath):
    """
    Returns the scan numbers from the given filepath.

    The scan numbers are stored in the filename of the given filepath
    as 'XYY.ras', where X is the scan number in the x direction and
    Y is the scan number in the y direction.

    Parameters
    ----------
    filepath : str
        The filepath to the XRD data file (.ras)

    Returns
    -------
    tuple
        A tuple containing the x and y scan numbers.
    """
    idxes = (filepath.name).split(".ras")[0].split("_")[-1]
    x_idx, y_idx = idxes[:3], idxes[3:]

    return int(x_idx), int(y_idx)


def read_data_from_ras(filepath):
    """
    Reads a .ras file and returns the following dictionaries and a list:

    disp_dict: A dictionary containing the values of the *DISP_ keywords
    file_dict: A dictionary containing the values of the *FILE_ keywords
    hw_dict: A dictionary containing the values of the *HW_ keywords
    meas_dict: A dictionary containing the values of the *MEAS_ keywords
    data: A list of lists, where each sublist contains the data for a specific angle

    Returns
    -------
    tuple
        A tuple containing the disp_dict, file_dict, hw_dict, meas_dict, and data
    """
    with open(filepath, "r", encoding="iso-8859-1") as f:
        parse_ignore = [
            "*RAS_DATA_START",
            "*RAS_HEADER_START",
            "*RAS_HEADER_END",
            "*RAS_INT_START",
            "*RAS_INT_END",
            "*RAS_TEMPERATURE_START",
            "*RAS_TEMPERATURE_END",
            "*RAS_DATA_END",
        ]
        disp_dict = {}
        file_dict = {}
        hw_dict = {}
        meas_dict = {}
        data = []

        # Split the header into different metadata types
        for line in f:
            if line.startswith("*"):
                formatted_line = line.strip().split(" ", 1)
                if formatted_line[0] not in parse_ignore:
                    if formatted_line[0].startswith("*DISP"):
                        key = formatted_line[0].replace("*DISP_", "")
                        disp_dict[key] = formatted_line[1]

                    elif formatted_line[0].startswith("*FILE"):
                        key = formatted_line[0].replace("*FILE_", "")
                        file_dict[key] = formatted_line[1]

                    elif formatted_line[0].startswith("*HW"):
                        key = formatted_line[0].replace("*HW_", "")
                        hw_dict[key] = formatted_line[1]

                    elif formatted_line[0].startswith("*MEAS"):
                        if "INTERNAL" in formatted_line[0]:
                            continue
                        else:
                            key = formatted_line[0].replace("*MEAS_", "")
                            meas_dict[key] = formatted_line[1]

            else:
                # Read the data inside the file
                data.append([[elm] for elm in line.strip().split(" ")])

        return disp_dict, file_dict, hw_dict, meas_dict, data


def get_results_from_refinement(filepath):
    """
    Reads a .lst file and returns the following dictionaries:

    r_coeffs: A dictionary containing the R-factors from the refinement.
    global_params: A dictionary containing the global parameters from the refinement.
    phases: A dictionary containing the parameters for each phase, including the atomic positions.

    Parameters
    ----------
    filepath : str or Path
        The filepath to the .lst file

    Returns
    -------
    tuple
        A tuple containing the r_coeffs, global_params, and phases dictionaries
    """
    fullpath = filepath.parent / filepath.name.replace(".ras", ".lst")
    attrib_list = [
        "SpacegroupNo=",
        "HermannMauguin=",
        "XrayDensity=",
        "Rphase=",
        "UNIT=",
        "A=",
        "B=",
        "C=",
        "k1=",
        "k2=",
        "B1=",
    ]
    r_coeffs, global_params, phases = {}, {}, {}
    current_phase = "None"

    with open(fullpath, "r") as file:
        for line in file:
            # Parse the result file from the refinement
            if line.startswith("Rp="):
                R_factors = line.split()
                for elm in [elm.strip().split("=") for elm in R_factors]:
                    r_coeffs[elm[0]] = elm[1]
            elif line.startswith("Q"):
                elm = line.strip().split("=")
                global_params[elm[0]] = elm[1]
            elif line.startswith("Local parameters and GOALs for phase"):
                current_phase = line.split()[-1]
                phases[current_phase] = {}
            elif True in [line.startswith(elm) for elm in attrib_list]:
                elm = line.strip().split("=")
                phases[current_phase][elm[0]] = convertFloat(elm[1])
            elif line.startswith("GEWICHT="):
                elm = [elm.split("=") for elm in line.strip().split(", ")]
                phases[current_phase][elm[0][0]] = elm[0][1]
                # Check if the mean value of GEWICHT was also calculated
                if len(elm) > 1:
                    phases[current_phase][elm[1][0]] = float(elm[1][1])
            elif line.startswith("Atomic positions for phase"):
                phases[current_phase]["Atomic positions"] = {}
                atomic_positions = []
                file.readline()

                while True:
                    new_line = file.readline().strip()
                    if len(new_line) <= 0:
                        break
                    atomic_positions.append(new_line)
                phases[current_phase]["Atomic positions"] = atomic_positions

    return r_coeffs, global_params, phases


def set_instrument_and_result_from_dict(xrd_dict, node):
    """
    Writes the contents of the xrd_dict dictionary to the HDF5 node.

    Args:
        xrd_dict (dict): A dictionary containing the XRD data and metadata, generated by the read_data_from_ras function.
        node (h5py.Group): The HDF5 group to write the data to.
    Returns:
        None
    """

    for key, value in xrd_dict.items():
        if isinstance(value, dict):
            set_instrument_and_result_from_dict(value, node.create_group(key))
        elif isinstance(value, str):
            if key == "UNIT":
                continue
            node[key] = value.replace('"', "")
            if key in ["A", "B", "C"]:
                node[key].attrs["units"] = "nm"
        elif isinstance(value, list):
            node.create_dataset(key, data=value)
        else:
            node[key] = value

    return None


def get_2dcamera_from_img(filepath):
    """
    Reads the header and data of a 2D detector image file and returns them as a tuple.

    Parameters
    ----------
    filepath : pathlib.Path
        The path to the .ras file of the XRD measurement.

    Returns
    -------
    tuple
        A tuple containing the header and data of the 2D detector image, as read from the file.
    """

    prefix = filepath.name.strip(".ras")

    for file in os.listdir(filepath.parent):
        if prefix in file and file.endswith(".img"):
            fullpath = filepath.parent / file
            with open(fullpath, "rb") as f:
                img = fabio.open(f)
                img_header = img.header
                img_data = img.data
            break

    return img_header, img_data


def write_xrd_to_hdf5(HDF5_path, filepath, mode="a", exclude_wafer_edges=True):
    """
    Writes the contents of the XRD data file (.ras) to the given HDF5 file.

    Args:
        HDF5_path (str or Path): The path to the HDF5 file to write the data to.
        filepath (str or Path): The path to the XRD data file (.ras).
        mode (str, optional): The mode to open the HDF5 file in. Defaults to "a".

    Returns:
        None
    """
    scan_numbers = get_scan_numbers(filepath)
    disp_dict, file_dict, hw_dict, meas_dict, data_dict = read_data_from_ras(filepath)
    x_pos, y_pos = meas_dict["COND_AXIS_POSITION-6"].replace('"', ""), meas_dict[
        "COND_AXIS_POSITION-7"
    ].replace('"', "")

    # Remove points outside and very close to the edges
    if is_outside_wafer(x_pos, y_pos) and exclude_wafer_edges:
        return None

    r_coeffs_dict, global_params_dict, phases_dict = get_results_from_refinement(
        filepath
    )
    img_header, img_data = get_2dcamera_from_img(filepath)

    with h5py.File(HDF5_path, mode) as f:
        scan_group = f"/entry/xrd/scan_{scan_numbers[0]},{scan_numbers[1]}/"
        scan = f.create_group(scan_group)

        # Instrument group for metadata
        instrument = scan.create_group("instrument")
        instrument.attrs["NX_class"] = "HTinstrument"
        instrument["x_pos"] = convertFloat(x_pos)
        instrument["y_pos"] = convertFloat(y_pos)
        instrument["x_pos"].attrs["units"] = "mm"
        instrument["y_pos"].attrs["units"] = "mm"

        # Separating the header into 4 groups for clarity
        disp, file, hw, meas = create_multiple_groups(
            instrument, ["disp", "file", "hardware", "meas"]
        )
        set_instrument_and_result_from_dict(disp_dict, disp)
        set_instrument_and_result_from_dict(file_dict, file)
        set_instrument_and_result_from_dict(hw_dict, hw)
        set_instrument_and_result_from_dict(meas_dict, meas)

        # Results group for metadata
        results = scan.create_group("results")
        results.attrs["NX_class"] = "HTresults"
        r_coefficients, global_parameters, phases = create_multiple_groups(
            results, ["r_coefficients", "global_parameters", "phases"]
        )
        set_instrument_and_result_from_dict(r_coeffs_dict, r_coefficients)
        set_instrument_and_result_from_dict(global_params_dict, global_parameters)
        set_instrument_and_result_from_dict(phases_dict, phases)

        # Data group
        data = scan.create_group("measurement")
        data.attrs["NX_class"] = "HTmeasurement"
        angle = [convertFloat(elm[0][0]) for elm in data_dict]
        counts = [convertFloat(elm[1][0]) for elm in data_dict]
        angle = data.create_dataset("angle", (len(angle),), data=angle, dtype="float")
        counts = data.create_dataset(
            "counts", (len(counts),), data=counts, dtype="float"
        )
        angle.attrs["units"] = "degrees"
        counts.attrs["units"] = "counts"

        # Image group
        image = scan.create_group("image")
        image.attrs["NX_class"] = "HTimage"

        set_instrument_and_result_from_dict(img_header, image)
        image.create_dataset("2D_Camera_Image", img_data.shape, data=img_data)

    return None
