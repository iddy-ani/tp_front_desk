"""
requirements: pandas,pymongo
"""

import ast
import re
import traceback
import time
import os
import stat
import json
import pandas as pd
from collections import defaultdict
from typing import Optional
from datetime import datetime

import pymongo
from pydantic import BaseModel, Field

# NOTE: Removed hardcoded credentials. Provide Mongo URI via either environment variable
# THERMAL_AUDIT_MONGO_URI or through tool Valves (mongo_uri). Database name defaults to axel_egpt
# but can be overridden via Valves.

SUBMODULE_NAME = "thermal_audit"

OUTPUT_DIR = os.path.join("Outputs", "ThermalAuditResults")
LAST_AUDIT_CONTEXT_FILE = os.path.join(OUTPUT_DIR, "last_audit_context.json")


def _format_missing_inputs(missing_fields: list[str]) -> str:
    missing_list = ", ".join(missing_fields)
    return (
        f"Could you provide the following required value(s) so I can continue? {missing_list}. "
        "Once you share them I can rerun the command."
    )


def _ensure_output_dir() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _get_abs_output_path(*parts: str) -> str:
    _ensure_output_dir()
    return os.path.abspath(os.path.join(OUTPUT_DIR, *parts))


def _write_last_audit_context(cleaned_results: dict, csv_path: str) -> None:
    context = {
        "tp_name": cleaned_results.get("TestProgram") or cleaned_results.get("TP_Name"),
        "tp_path": cleaned_results.get("TPPath") or cleaned_results.get("TP_Path"),
        "product_code": cleaned_results.get("ProductCode"),
        "submitted_by": cleaned_results.get("SubmittedBy"),
        "open_tat_link": cleaned_results.get("OpenTATWebLink"),
        "csv_path": os.path.abspath(csv_path),
        "generated_at": datetime.utcnow().isoformat(),
        "rules": sorted(
            {
                item.get("Rule")
                for item in cleaned_results.get("Items", [])
                if isinstance(item, dict) and item.get("Rule")
            }
        ),
    }
    try:
        with open(LAST_AUDIT_CONTEXT_FILE, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2, default=str)
    except Exception:
        pass


def _load_last_audit_context() -> dict:
    try:
        with open(LAST_AUDIT_CONTEXT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def _build_markdown_table(results_dict: dict) -> str:
    if not results_dict:
        return "No failing items identified."
    table_header = "| Key | Value |\n|---|---|\n"
    table_rows = "\n".join([f"| {key} | {value} |" for key, value in results_dict.items()])
    return table_header + table_rows


def _select_csv_file(tp_name: Optional[str]) -> Optional[str]:
    if not os.path.exists(OUTPUT_DIR):
        return None
    if tp_name:
        candidate = os.path.join(OUTPUT_DIR, f"{tp_name}.csv")
        if os.path.exists(candidate):
            return candidate
    csv_paths = [
        os.path.join(OUTPUT_DIR, f)
        for f in os.listdir(OUTPUT_DIR)
        if f.lower().endswith(".csv")
    ]
    if len(csv_paths) == 1:
        return csv_paths[0]
    if tp_name:
        lowered = tp_name.lower()
        for path in csv_paths:
            if os.path.basename(path).lower().startswith(lowered):
                return path
    return None


def _export_audit_artifacts(payload: dict) -> tuple[str, str, dict]:
    cleaned_results = clean_data(payload)
    failing_items_dict = filter_failing_items(cleaned_results)
    _ensure_output_dir()

    tp_name = cleaned_results.get("TestProgram") or cleaned_results.get("TP_Name") or "ThermalAudit"
    csv_path = _get_abs_output_path(f"{tp_name}.csv")

    items_for_csv = cleaned_results.get("Items", [])
    open_tat_link = cleaned_results.get("OpenTATWebLink")
    if isinstance(items_for_csv, list) and all(isinstance(i, dict) for i in items_for_csv):
        if open_tat_link is not None:
            for item in items_for_csv:
                item["OpenTATWebLink"] = open_tat_link
        df = pd.DataFrame(items_for_csv)
    else:
        df = pd.DataFrame([cleaned_results])
    df.to_csv(csv_path, index=False)

    _write_last_audit_context(cleaned_results, csv_path)

    results_for_table = dict(failing_items_dict) if isinstance(failing_items_dict, dict) else {}
    markdown_table = _build_markdown_table(results_for_table)
    return csv_path, markdown_table, cleaned_results


# MongoDB connection URIs
mongo_uri_axel_egpt = 'mongodb://axel_egpt_rw:eRmVhUp05SfSnXm@p1ir1mon019.ger.corp.intel.com:7177,p2ir1mon019.ger.corp.intel.com:7177,p3ir1mon019.ger.corp.intel.com:7177/axel_egpt?ssl=true&replicaSet=mongo7177' 
mongo_uri_thermal = 'mongodb://Thermals_ro:91g38XuEnZzWk7e@p1ir1mon019.ger.corp.intel.com:7177,p2ir1mon019.ger.corp.intel.com:7177,p3ir1mon019.ger.corp.intel.com:7177/Thermals?ssl=true&replicaSet=mongo7177'
mongo_uri_axel = 'mongodb://AxelDB_rw:r28Z0F1U48s8yZ0@p1ir1mon014.ger.corp.intel.com:7125,p2ir1mon014.ger.corp.intel.com:7125,p3ir1mon014.ger.corp.intel.com:7125/AxelDB?ssl=true&replicaSet=mongo7125'

# Database names
axel_egpt_db_name = "axel_egpt"
thermal_db_name = "Thermals"
axel_db_name = "AxelDB"

def _get_mongo_client(mongo_uri: str) -> pymongo.MongoClient:
    """Get MongoDB client with error handling."""
    try:
        return pymongo.MongoClient(mongo_uri)
    except Exception as e:
        raise RuntimeError(f"Failed to connect to MongoDB: {e}")

def _get_collections():
    """Get MongoDB collections for thermal audit operations."""
    thermal_client = _get_mongo_client(mongo_uri_thermal)
    axel_egpt_client = _get_mongo_client(mongo_uri_axel_egpt)
    axel_client = _get_mongo_client(mongo_uri_axel)
    
    thermal_db = thermal_client[thermal_db_name]
    axel_egpt_db = axel_egpt_client[axel_egpt_db_name]
    axel_db = axel_client[axel_db_name]

    thermal_results_collection = thermal_db['Results']
    tat_results_collection = axel_egpt_db['thermalAuditResults']
    axel_jobs_collection = axel_db['AxelJobs']
    thermal_audit_dashboard_collection = axel_db['ThermalAudit_Dashboard']
    
    return thermal_results_collection, tat_results_collection, axel_jobs_collection, thermal_audit_dashboard_collection


def _validate_required_inputs(required_inputs: dict[str, Optional[str]]) -> tuple[list[str], dict[str, str]]:
    """Normalize string inputs and report which required values are missing."""
    normalized: dict[str, str] = {}
    missing: list[str] = []
    for field, value in required_inputs.items():
        cleaned_value = value.strip() if isinstance(value, str) else value
        if not cleaned_value:
            missing.append(field)
        else:
            normalized[field] = cleaned_value
    return missing, normalized


def _is_completed_status(status: Optional[str]) -> bool:
    """Return True when the job status indicates completion."""
    if not isinstance(status, str):
        return False
    return status.strip().lower() in {"completed", "complete"}


def _get_alert_messages(alerts_field) -> list[str]:
    """Normalize the alerts payload into a list of strings."""
    if isinstance(alerts_field, list):
        return [str(alert).strip() for alert in alerts_field if isinstance(alert, str) and alert.strip()]
    if isinstance(alerts_field, str) and alerts_field.strip():
        return [alerts_field.strip()]
    return []


def _extract_setting_differences(explaination_field: Optional[str]) -> dict[str, str]:
    """Parse explanation text to map parameters to their 'Right Settings' values."""
    if not explaination_field:
        return {}

    entries: list[str] = []
    cleaned = explaination_field.strip()
    if not cleaned:
        return {}

    try:
        parsed = ast.literal_eval(cleaned)
        if isinstance(parsed, list):
            entries = [str(item) for item in parsed]
        else:
            entries = [cleaned]
    except Exception:
        entries = [cleaned]

    diffs: dict[str, str] = {}
    right_pattern = re.compile(r"Right Settings\s*>\s*([^=%]+?)\s*=\s*([^%]+)")

    for entry in entries:
        for match in right_pattern.finditer(entry):
            key = match.group(1).strip()
            value = match.group(2).strip().rstrip('%').strip()
            if key and value:
                diffs[key] = value
    return diffs


def _infer_test_method_from_module(module_source: Optional[str], test_name: str) -> Optional[str]:
    if not module_source or not test_name:
        return None
    sanitized_name = test_name.split("::")[-1]
    pattern = re.compile(rf"Test\s+(\w+)\s+{re.escape(sanitized_name)}")
    match = pattern.search(module_source)
    return match.group(1) if match else None


def _prepare_template_param_value(value: str) -> str:
    if not isinstance(value, str):
        return str(value)
    stripped = value.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        return stripped
    if re.fullmatch(r"[-+]?[0-9]*\.?[0-9]+", stripped):
        return stripped
    return f'"{stripped}"'


def _extract_uservar_params(structured_change: Optional[dict], module_name: str) -> dict[str, str]:
    """Identify which template parameters reference module user variables."""
    if not structured_change:
        return {}

    mapping: dict[str, str] = {}
    parameters = structured_change.get("Parameters", {})
    for param, value in parameters.items():
        if not isinstance(value, str):
            continue
        match = re.search(r"::[^.]+\.(\w+)", value)
        if match:
            mapping[param] = match.group(1)
    return mapping


def _format_uservar_value(value: str, var_type: str) -> str:
    if var_type.lower() == "string":
        cleaned = value.strip().strip('"')
        return f'"{cleaned}"'
    return value


def _guess_uservar_type(value: str) -> str:
    stripped = value.strip()
    try:
        int(stripped)
        return "Integer"
    except ValueError:
        try:
            float(stripped)
            return "Double"
        except ValueError:
            return "String"


def _apply_uservar_updates_to_file(file_path: str, module_name: str, updates: dict[str, dict]) -> list[str]:
    results: list[str] = []
    if not os.path.exists(file_path):
        return [f"✗ {os.path.basename(file_path)} not found; unable to update user variables."]

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    block_start = None
    block_end = None
    for idx, line in enumerate(lines):
        if line.strip().startswith(f"UserVars {module_name}"):
            block_start = idx
            break
    if block_start is None:
        return [f"✗ UserVars block for {module_name} not found in {os.path.basename(file_path)}"]

    for idx in range(block_start, len(lines)):
        if lines[idx].strip().startswith('}'):
            block_end = idx
            break
    if block_end is None:
        return [f"✗ Closing brace for {module_name} user vars not found in {os.path.basename(file_path)}"]

    entry_pattern = re.compile(r"^(\s*)(String|Integer|Double)\s+(\w+)\s*=\s*(.+);")
    existing_entries: dict[str, dict] = {}
    for idx in range(block_start, block_end):
        match = entry_pattern.match(lines[idx])
        if match:
            indent, var_type, name, current_value = match.groups()
            existing_entries[name] = {
                "line": idx,
                "type": var_type,
                "indent": indent,
            }

    default_indent = existing_entries[next(iter(existing_entries))]["indent"] if existing_entries else "\t"

    for var_name, meta in updates.items():
        desired_value = meta["value"]
        source_rule = meta.get("rule", "?")
        var_type = meta.get("type")

        if var_name in existing_entries:
            info = existing_entries[var_name]
            current_type = info["type"]
            line_idx = info["line"]
            formatted_value = _format_uservar_value(desired_value, current_type)
            lines[line_idx] = f"{info['indent']}{current_type} {var_name} = {formatted_value};\n"
            results.append(f"✓ Updated {module_name}.usrv::{var_name} (Rule {source_rule})")
        else:
            chosen_type = var_type or _guess_uservar_type(desired_value)
            formatted_value = _format_uservar_value(desired_value, chosen_type)
            lines.insert(block_end, f"{default_indent}{chosen_type} {var_name} = {formatted_value};\n")
            block_end += 1
            results.append(f"+ Added {module_name}.usrv::{var_name} (Rule {source_rule})")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    return results


def _collect_uservar_updates(filtered_df, template_change_map) -> tuple[dict[str, dict[str, dict]], list[dict]]:
    updates: dict[str, dict[str, dict]] = defaultdict(dict)
    audit_entries: list[dict] = []

    for idx, row in filtered_df.iterrows():
        parsed_change = template_change_map.get(idx)
        if not parsed_change:
            continue

        diffs = _extract_setting_differences(row.get("Explaination", ""))
        if not diffs:
            continue

        module_name = row.get("ModuleName", "")
        param_to_uservar = _extract_uservar_params(parsed_change, module_name)
        if not param_to_uservar:
            continue

        for param, desired_value in diffs.items():
            uservar_name = param_to_uservar.get(param)
            if not uservar_name:
                continue
            updates[module_name][uservar_name] = {
                "value": desired_value,
                "rule": row.get("Rule"),
            }
            audit_entries.append(
                {
                    "ModuleName": module_name,
                    "UserVar": uservar_name,
                    "Parameter": param,
                    "NewValue": desired_value,
                    "Rule": row.get("Rule"),
                }
            )

    return updates, audit_entries


def _apply_uservar_updates(output_dir: str, uservar_updates: dict[str, dict[str, dict]]) -> tuple[list[str], list[dict]]:
    results: list[str] = []
    applied_entries: list[dict] = []

    for module_name, updates in uservar_updates.items():
        edited_usrv_path = os.path.join(output_dir, f"edited_{module_name}.usrv")
        module_results = _apply_uservar_updates_to_file(edited_usrv_path, module_name, updates)
        results.extend(module_results)
        for var_name, meta in updates.items():
            applied_entries.append(
                {
                    "ModuleName": module_name,
                    "UserVar": var_name,
                    "NewValue": meta["value"],
                    "Rule": meta.get("rule"),
                }
            )

    return results, applied_entries

def clean_data(d):
    """Recursively remove all keys containing '_id' from a dict, remove dicts with 'Explaination' == None from lists, and remove 'Explaination' key if its value is None."""
    if isinstance(d, dict):
        # Remove '_id' keys and recursively clean
        cleaned = {k: clean_data(v) for k, v in d.items() if '_id' not in k}
        # Remove 'Explaination' key if its value is None
        if 'Explaination' in cleaned and cleaned['Explaination'] is None:
            cleaned.pop('Explaination')
        return cleaned
    elif isinstance(d, list):
        cleaned_list = [clean_data(i) for i in d]
        cleaned_list = [item for item in cleaned_list if not (isinstance(item, dict) and item.get("Explaination", None) is None)]
        return cleaned_list
    else:
        return d
    
def filter_failing_items(d):
    """Recursively filter data structure to keep only items where 'Valid' is False, preserving structure."""
    if isinstance(d, dict):
        filtered_dict = {}
        for k, v in d.items():
            if k == "Items" and isinstance(v, list):
                # Special handling for Items list - filter out Valid=True items
                filtered_items = [item for item in v if isinstance(item, dict) and item.get("Valid") is False]
                if filtered_items:  # Only include if there are failing items
                    filtered_dict[k] = filtered_items
            elif isinstance(v, (dict, list)):
                filtered_value = filter_failing_items(v)
                if filtered_value:  # Only include non-empty results
                    filtered_dict[k] = filtered_value
            else:
                # Include non-container values as-is
                filtered_dict[k] = v
        return filtered_dict
    elif isinstance(d, list):
        filtered_list = []
        for item in d:
            if isinstance(item, dict):
                if item.get("Valid") is False:
                    filtered_list.append(item)
                else:
                    # Recursively filter nested structures
                    filtered_item = filter_failing_items(item)
                    if filtered_item:
                        filtered_list.append(filtered_item)
            elif isinstance(item, list):
                filtered_item = filter_failing_items(item)
                if filtered_item:
                    filtered_list.append(filtered_item)
            else:
                filtered_list.append(item)
        return filtered_list
    else:
        return d

def check_thermal_audit_completion_status(tp_name: str) -> str:
    """Check the completion status of a thermal audit job."""
    try:
        thermal_results_collection, tat_results_collection, axel_jobs_collection, thermal_audit_dashboard_collection = _get_collections()
        
        document = thermal_results_collection.find_one({"TP_Name": tp_name})
        if not document:
            return f"No thermal results found for Test Program '{tp_name}'. I can submit a Thermal audit job for you or you can contact axel_adtl@intel.com for assistance."
        
        status = document.get("JobStatus", "Not_Started")
        normalized_status = status.strip() if isinstance(status, str) else "Not_Started"
        alert_messages = _get_alert_messages(document.get("Alerts"))

        if alert_messages:
            alert_text = "\n".join(alert_messages)
            return (
                "Thermal Audit reported error(s):\n"
                f"{alert_text}\n"
                "Please verify the inputs (TP path, product info, and network access) or contact axel_adtl@intel.com for assistance."
            )

        if normalized_status.lower() == 'not_started':
            return f"Thermal Audit has not yet completed. If you have submitted a Thermal Audit job and more than 5 minutes have passed please contact axel_adtl@intel.com for assistance. Otherwise, I can submit a Thermal audit job for you."

        # Status is Completed, get results
        if not _is_completed_status(status):
            return f"Thermal Audit job is in '{normalized_status}' state. Please wait for completion or contact axel_adtl@intel.com for assistance."

        clean_results = _perform_thermal_audit(tp_name)
        return f'Thermal Audit completed: {clean_results}'
    except Exception as ex:
        return f"Exception occurred: {str(ex)}"

def submit_tp_for_audit(
    __user__: dict,
    tp_path: str,
    tp_name: str,
    product_name: Optional[str],
    prod_code: str,
    poll_interval: int,
    max_wait: int,
    wait: bool = False,
) -> str:
    """Submit a test program for thermal audit."""
    try:
        required_inputs = {
            "tp_path": tp_path,
            "tp_name": tp_name,
            "product_name": product_name,
            "prod_code": prod_code,
        }
        missing_fields, normalized = _validate_required_inputs(required_inputs)
        if missing_fields:
            return _format_missing_inputs(missing_fields)

        if poll_interval <= 0 or max_wait <= 0:
            return "poll_interval and max_wait must be positive integers."

        resolved_tp_path = normalized["tp_path"]
        resolved_tp_name = normalized["tp_name"]
        resolved_product = normalized["product_name"]
        resolved_code = normalized["prod_code"]

        thermal_results_collection, tat_results_collection, axel_jobs_collection, thermal_audit_dashboard_collection = _get_collections()

        job_filter = {"TestProgram": resolved_tp_name}
        try:
            tat_results_collection.delete_many(job_filter)
        except Exception:
            pass
        try:
            axel_jobs_collection.delete_many(job_filter)
        except Exception:
            pass
        try:
            thermal_audit_dashboard_collection.delete_many(job_filter)
        except Exception:
            pass

        thermal_audit_dashboard_payload = {
            "Product" : resolved_product,
            "ProductCode" : resolved_code,
            "Alerts" : [
        
            ],
            "CreationDate" : datetime.utcnow(),
            "FabSite" : None,
            "SortSite" : None,
            "JobStatus" : "Not_Started",
            "TestProgram" : tp_name,
            "CreatedBy" : __user__.get('name', 'Unknown'),
            "CreatorEmail" : __user__.get('email', 'unknown@intel.com'),
            "WW" : None,
            "Result" : None
        }
        
        dashboard_result = thermal_audit_dashboard_collection.insert_one(thermal_audit_dashboard_payload)

        axel_db_payload = {
            "_id": dashboard_result.inserted_id,
            "JobType": 0,
            "TP_Path": resolved_tp_path,
            "CustomADTLParserDLLPath": "",
            "XParameterName": "",
            "OutputPath": None,
            "TestExcludeList": [],
            "Pre_QS": None,
            "Include_Flow": [

            ],
            "AuditApprovedJsonFile": "",
            "ProductName": resolved_product,
            "ProductFamily": None,
            "PreviousGoldenJsonFileForReference": "",
            "Emails": [
                __user__.get('email', 'unknown@intel.com'),
                "axel_adtl_team@intel.com"
            ],
            "Lots": [
                
            ],
            "Operation": None,
            "ProductCode": resolved_code,
            "StartYearWW": "",
            "EndYearWW": "",
            "Site": None,
            "Source": None,
            "TestProgram": resolved_tp_name,
            "MaxDieCount": 0,
            "MaxWaferCount": 0,
            "UseGoodDieOnly": False,
            "Multi_Program": False,
            "UseProductionLotsOnly": True,
            "Additional_TPs": [],
            "Bins": [
                
            ]
        }

        submission_result = axel_jobs_collection.insert_one(axel_db_payload)
        if submission_result.inserted_id and dashboard_result.inserted_id:
        
            if wait:
                start_time = time.monotonic()
                status = "Not_Started"
                normalized_dashboard_status = "Not_Started"
                while True:
                    document = thermal_audit_dashboard_collection.find_one({"_id": dashboard_result.inserted_id})
                    if document:
                        status = document.get("JobStatus", "Not_Started")
                        normalized_dashboard_status = status.strip() if isinstance(status, str) else "Not_Started"
                        alert_messages = _get_alert_messages(document.get("Alerts"))
                        if alert_messages:
                            alert_text = "\n".join(alert_messages)
                            return (
                                "Thermal Audit reported error(s) during execution:\n"
                                f"{alert_text}\n"
                                "Please verify the inputs or contact axel_adtl@intel.com for assistance."
                            )
                        if _is_completed_status(status) or normalized_dashboard_status.lower() == "error":
                            break
                    if time.monotonic() - start_time > max_wait:
                        return f"Timeout waiting for audit completion after {max_wait}s. Last status: {normalized_dashboard_status}. The API may be down, please contact axel_adtl@intel.com for assistance."
                    time.sleep(poll_interval)

                if normalized_dashboard_status.lower() == "error":
                    return f"Error in Thermal Audit submodule: {document.get('ErrorMessage', 'Unknown error')}"

                # Status is Completed, get results
                clean_results = _perform_thermal_audit(resolved_tp_name)
                return f'Thermal Audit submitted and completed: {clean_results}'
            else:
                return f'Thermal Audit for {resolved_tp_name} has been submitted successfully. RunID: {dashboard_result.inserted_id}'
        else:
            return "Failed to submit Thermal Audit job. Please submit the job manually at https://axel.app.intel.com/ or contact axel_adtl@intel.com for assistance."
    except Exception as ex:
        return f"Exception occurred: {str(ex)}"

def _perform_thermal_audit(tp_name: str) -> str:
    """Process thermal audit results and generate CSV/markdown output."""
    try:
        thermal_results_collection, tat_results_collection, axel_jobs_collection, thermal_audit_dashboard_collection = _get_collections()
        
        # Find thermal result by TestProgram name and aggregate all matching documents
        cursor = thermal_results_collection.find({"TP_Name": tp_name})
        items = [doc for doc in cursor]
        if not items:
            return f"No thermal results found for Test Program '{tp_name}'. I can submit a Thermal audit job for you or you can contact axel_adtl@intel.com for assistance."
        
        open_tat_link = f"https://axel.app.intel.com/thermalAuditResult/{tp_name}"

        job_doc = axel_jobs_collection.find_one({"TestProgram": tp_name}) or {}
        dashboard_doc = thermal_audit_dashboard_collection.find_one({"TestProgram": tp_name}) or {}

        tp_path = job_doc.get("TP_Path", "Unknown")
        prod_code = dashboard_doc.get("ProductCode", "Unknown")
        creator_email = dashboard_doc.get("CreatorEmail")
        if isinstance(creator_email, list):
            email = creator_email[0] if creator_email else "unknown@intel.com"
        else:
            email = creator_email or "unknown@intel.com"

        payload = {
            "Items": items,
            "ProductCode": prod_code,
            "TestProgram": tp_name,
            "SubmittedBy": email,
            "CompletionDate": datetime.utcnow(),
            "TPPath": tp_path,
            "OpenTATWebLink": open_tat_link,
        }

        try:
            tat_results_collection.replace_one(
                {"TestProgram": tp_name},
                payload,
                upsert=True,
            )
        except Exception:
            pass

        csv_path, markdown_table, cleaned_results = _export_audit_artifacts(payload)
        open_tat_link = cleaned_results.get("OpenTATWebLink")

        response_lines = []
        if open_tat_link:
            response_lines.append(f"You can view the audit results at this link: https://axel.app.intel.com/thermalAuditResult/{tp_name}")
        response_lines.append(f"Results saved to: {csv_path}.")
        response_lines.append("Results (failing items focus):\n" + markdown_table)
        response_text = "\n\n".join(response_lines)
        return response_text
    except Exception as e:
        return f"Error processing thermal audit results: {str(e)}. Please contact axel_adtl@intel.com for assistance."

def _get_thermal_audit(tp_name: str) -> str:
    try:
        thermal_results_collection, tat_results_collection, axel_jobs_collection, thermal_audit_dashboard_collection = _get_collections()

        results = tat_results_collection.find_one({"TestProgram": tp_name})
        if not results:
            return f"No thermal audit results found for Test Program '{tp_name}'. Would you like me to run a Thermal Audit for {tp_name}?"
        csv_path, _, cleaned_results = _export_audit_artifacts(results)
        return (
            f"Here is the last thermal summary run for {tp_name} on {results.get('CompletionDate', 'unknown date')}:\n\n"
            f"{json.dumps(cleaned_results, default=str, indent=2)}\n\n"
            f"The CSV snapshot has been refreshed at {csv_path}. If you need a new run let me know and I can queue it."
        )
    except Exception as e:
        return f"Error retriving thermal audit results for {tp_name}: {str(e)}. Please contact axel_adtl@intel.com for assistance."

class Tools:
    """Thermal Audit tooling for DataAgent Web UI.

    Functions:
    - thermal_audit(tp_path, tp_name): Run audit and produce CSV + markdown summary.
    - apply_mtpl_updates(tp_path, rules): Apply MTPL/UserVar changes from prior audit.
    - check_completion_status(tp_name): Check status of existing thermal audit job.
    - submit_for_audit(tp_path, tp_name, wait): Submit new thermal audit job with optional wait.

    Configure connection/poll behavior using Valves or environment variables.
    """

    class Valves(BaseModel):  # Admin-level adjustable settings
        poll_interval_seconds: int = Field(
            default=5, ge=1, le=60, description="Polling interval while waiting for audit completion."
        )
        max_wait_seconds: int = Field(
            default=600, ge=30, le=3600, description="Maximum time to wait for audit completion before timing out."
        )

    def get_last_thermal_audit_snapshot(self, tp_name: str, __user__={}, __id__="") -> str:
        """Retrieve thermal audit results for the specified Test Program.

        :param tp_name: Name/identifier of the test program.
        :return: String with results or error message.
        """
        try:
            missing, normalized = _validate_required_inputs({"tp_name": tp_name})
            if missing:
                return _format_missing_inputs(missing)
            return _get_thermal_audit(normalized["tp_name"])
        except Exception:
            return f"Error retrieving Thermal Audit results: {str(traceback.format_exc())}"

    def get_thermal_audit(self, tp_name: str, __user__={}, __id__="") -> str:
        """Alias wrapper so tools can request the latest thermal audit snapshot."""
        return self.get_last_thermal_audit_snapshot(tp_name, __user__, __id__)


    def thermal_audit(self, tp_name: str, __user__={}, __id__="") -> str:
        """Run the thermal audit against the specified Test Program path.

        :param tp_name: Name/identifier of the test program.
        :return: String with results or error message.
        """
        try:
            missing, normalized = _validate_required_inputs({"tp_name": tp_name})
            if missing:
                return _format_missing_inputs(missing)
            status_result = check_thermal_audit_completion_status(normalized["tp_name"])
            if status_result:
                return status_result
        except Exception:
            return f"Error in Thermal Audit submodule: {str(traceback.format_exc())}"
    
    def apply_mtpl_updates(self, tp_path: str = "", rules: str = "", tp_name: str = "", __user__={}, __id__="") -> str:
        """Apply MTPL / UserVar updates based on prior audit results.

        :param tp_path: Test program root path.
        :param rules: Comma-separated rule IDs to apply (without trailing 'p').
        :return: String with results or error message.
        """
        arguments = {"tp_path": tp_path, "rules": rules, "tp_name": tp_name}
        return update_mtpl_usrv(arguments)

    def copy_edits_to_test_program(self, tp_path: str, module_name: str, __user__={}, __id__="") -> str:
        try:
            missing, normalized = _validate_required_inputs({"tp_path": tp_path, "module_name": module_name})
            if missing:
                return _format_missing_inputs(missing)
            return copy_edited_module_back(normalized["tp_path"], normalized["module_name"])
        except Exception:
            return f"Error copying edited files: {str(traceback.format_exc())}"
    
    def check_completion_status(self, tp_name: str, __user__={}, __id__="") -> str:
        """Check the completion status of a thermal audit job.

        :param tp_name: Name/identifier of the test program to check status for.
        :return: String with status information or error message.
        """
        try:
            missing, normalized = _validate_required_inputs({"tp_name": tp_name})
            if missing:
                return _format_missing_inputs(missing)
            return check_thermal_audit_completion_status(normalized["tp_name"])
        except Exception:
            return f"Error checking thermal audit status: {str(traceback.format_exc())}"
    
    def submit_for_audit(
        self,
        tp_path: str,
        tp_name: str,
        product_name: Optional[str] = None,
        prod_code: str = "",
        wait: bool = True,
        __user__={},
        __id__="",
    ) -> str:
        """Submit a test program for thermal audit.

        :param tp_path: Full path to the test program directory (network drive).
        :param tp_name: Name/identifier of the test program.
        :param wait: Whether to wait for completion (default True) or just submit and return immediately.
        :return: String with submission results or error message.
        """
        try:
            required_inputs = {
                "tp_name": tp_name,
                "tp_path": tp_path,
            }
            missing, normalized = _validate_required_inputs(required_inputs)
            if missing:
                return _format_missing_inputs(missing)
            normalized["product_name"] = product_name or ""
            normalized["prod_code"] = prod_code or ""

            poll_interval = getattr(self, 'valves', None) and self.valves.poll_interval_seconds or 5
            max_wait = getattr(self, 'valves', None) and self.valves.max_wait_seconds or 600
            return submit_tp_for_audit(
                __user__,
                normalized["tp_path"],
                normalized["tp_name"],
                normalized.get("product_name"),
                normalized.get("prod_code"),
                poll_interval,
                max_wait,
                wait,
            )
        except Exception:
            return f"Error submitting thermal audit: {str(traceback.format_exc())}"

def copy_edited_module_back(tp_path: str, module_name: str) -> str:
    try:
        tp_path = tp_path.strip()
        module_name = module_name.strip()
        if not tp_path or not os.path.isdir(tp_path):
            return f"The provided path '{tp_path}' is not a valid directory."
        modules_root = os.path.join(tp_path, "Modules")
        if not os.path.isdir(modules_root):
            return f"The Modules directory was not found inside '{tp_path}'."
        module_dir = os.path.join(modules_root, module_name)
        if not os.path.isdir(module_dir):
            return f"Module '{module_name}' was not found inside {module_dir}."
        edited_mtpl = os.path.join(OUTPUT_DIR, f"edited_{module_name}.mtpl")
        edited_usrv = os.path.join(OUTPUT_DIR, f"edited_{module_name}.usrv")
        if not os.path.exists(edited_mtpl):
            return f"edited_{module_name}.mtpl is missing in {OUTPUT_DIR}. Please run the MTPL update first."
        dest_mtpl = os.path.join(module_dir, f"{module_name}.mtpl")
        import shutil
        shutil.copy2(edited_mtpl, dest_mtpl)
        result_lines = [f"✓ Copied {edited_mtpl} -> {dest_mtpl}"]
        if os.path.exists(edited_usrv):
            dest_usrv = os.path.join(module_dir, f"{module_name}.usrv")
            shutil.copy2(edited_usrv, dest_usrv)
            result_lines.append(f"✓ Copied {edited_usrv} -> {dest_usrv}")
        else:
            result_lines.append(f"- No edited uservar file found for {module_name}; skipped.")
        return "\n".join(result_lines)
    except Exception as exc:
        return f"Failed to copy edited files: {exc}"


def update_mtpl_usrv(arguments: dict) -> str:
    tp_path = (arguments.get("tp_path") or "").strip()
    tp_name = (arguments.get("tp_name") or "").strip()
    rules_str = (arguments.get("rules") or "").strip()

    context = _load_last_audit_context()
    if not tp_name:
        tp_name = context.get("tp_name", "")

    if not tp_name:
        return _format_missing_inputs(["tp_name"])

    csv_file_path = _select_csv_file(tp_name)
    attempt_notes: list[str] = []
    if not csv_file_path:
        attempt_notes.append(f"No cached CSV found for {tp_name}. Fetching the last snapshot...")
        attempt_notes.append(_get_thermal_audit(tp_name))
        csv_file_path = _select_csv_file(tp_name)

    if not csv_file_path:
        attempt_notes.append("Snapshot retrieval did not yield a CSV. Checking the current audit status...")
        attempt_notes.append(check_thermal_audit_completion_status(tp_name))
        csv_file_path = _select_csv_file(tp_name)

    if not csv_file_path:
        attempt_details = "\n".join(attempt_notes) if attempt_notes else "No cached audit data available."
        return (
            f"I couldn't locate thermal audit results for '{tp_name}'.\n\n{attempt_details}\n\n"
            "Please run a thermal audit or let me know if you'd like me to submit a new job."
        )

    if not tp_path:
        tp_path = context.get("tp_path", "")

    if not tp_path:
        return (
            "I located the latest audit results but still need the Test Program path (`tp_path`) "
            "so I can copy and edit the MTPL/UserVar files. Please share it and rerun the command."
        )

    if not os.path.isdir(tp_path):
        return f"The provided path '{tp_path}' is not a valid directory."

    try:
        df = pd.read_csv(csv_file_path)
    except Exception as e:
        return f"Error reading the CSV file at {csv_file_path}: {str(e)}"

    if df.empty:
        return "The CSV file is empty. Please run the thermal audit submodule first."

    if 'Rule' not in df.columns:
        return "The thermal audit CSV is missing the 'Rule' column, so I cannot determine what to update."

    available_rules = sorted({rule for rule in df['Rule'] if isinstance(rule, str) and rule.strip()})

    if rules_str:
        normalized_rules = []
        for rule in rules_str.split(","):
            cleaned = rule.strip()
            if not cleaned:
                continue
            normalized_rules.append(cleaned if cleaned.endswith('p') else f"{cleaned}p")
        rules_list = normalized_rules
    else:
        rules_list = available_rules

    if not rules_list:
        return (
            "I couldn't determine which rules need to be updated. Please specify them explicitly "
            "(for example: 'rules': '7,8.1') or rerun the audit to refresh the data."
        )

    filtered_df = df[df['Rule'].isin(rules_list)]
    expected_columns = ['Rule', 'ModuleName', 'TestName', 'TemplateChanges', 'UserVarChanges', 'Explaination']
    for column in expected_columns:
        if column not in filtered_df.columns:
            return f"The thermal audit CSV is missing the '{column}' column, so MTPL updates cannot proceed."
    filtered_df = filtered_df[expected_columns]

    if filtered_df.empty:
        return "No matching rules found in the thermal audit results."
    
    # get the unique ModuleNames from the filtered DataFrame
    unique_modules = filtered_df['ModuleName'].unique()
    module_sources: dict[str, str] = {}
        
    # in the tp_path navigate to Modules\<ModuleName> dir and copy the <ModuleName>.mtpl and <ModuleName>.usrv files to the Outputs/ThermalAuditResults directory
    for module_name in unique_modules:
        module_path = os.path.join(tp_path, "Modules", module_name)
        if not os.path.exists(module_path):
            return f"Module '{module_name}' does not exist in the Test Program directory."
        mtpl_file = os.path.join(module_path, f"{module_name}.mtpl")
        if not os.path.exists(mtpl_file):
            return f"Required mtpl file for module '{module_name}' is missing."
        with open(mtpl_file, 'r', encoding='utf-8') as src_file:
            module_sources[module_name] = src_file.read()

        usrv_file = os.path.join(module_path, f"{module_name}.usrv")
        is_usrv_file = os.path.exists(usrv_file)
        if not is_usrv_file:
            note = f"Required usrv file for module '{module_name}' is missing. This may not be an issue if the module does not use user variables."
        # robocopy the files to the Outputs/ThermalAuditResults directory. they are not csv they are custom files closer to json or dict format
        try:
            import shutil
            output_dir = OUTPUT_DIR
            os.makedirs(output_dir, exist_ok=True)
            mtpl_dest = os.path.join(output_dir, f"{module_name}.mtpl")
            # Copy the files
            if os.path.exists(mtpl_dest):
                os.chmod(mtpl_dest, stat.S_IWRITE | stat.S_IREAD)
            shutil.copy2(mtpl_file, mtpl_dest)
            # Now make a copy of the files and call it edited_<ModuleName>.mtpl and edited_<ModuleName>.usrv
            local_mtpl_file = os.path.join(output_dir, f"{module_name}.mtpl")
            edited_mtpl_dest = os.path.join(output_dir, f"edited_{module_name}.mtpl")
            if os.path.exists(edited_mtpl_dest):
                os.chmod(edited_mtpl_dest, stat.S_IWRITE | stat.S_IREAD)
            shutil.copy2(local_mtpl_file, edited_mtpl_dest)
            os.chmod(edited_mtpl_dest, stat.S_IWRITE | stat.S_IREAD)
            # If usrv_file exists, copy it as well
            if is_usrv_file:
                usrv_dest = os.path.join(output_dir, f"{module_name}.usrv")
                if os.path.exists(usrv_dest):
                    os.chmod(usrv_dest, stat.S_IWRITE | stat.S_IREAD)
                shutil.copy2(usrv_file, usrv_dest)
                local_usrv_file = os.path.join(output_dir, f"{module_name}.usrv")
                edited_usrv_dest = os.path.join(output_dir, f"edited_{module_name}.usrv")
                if os.path.exists(edited_usrv_dest):
                    os.chmod(edited_usrv_dest, stat.S_IWRITE | stat.S_IREAD)
                shutil.copy2(local_usrv_file, edited_usrv_dest)
                os.chmod(edited_usrv_dest, stat.S_IWRITE | stat.S_IREAD)  
        except Exception as e:
            return f"Error copying files for module '{module_name}': {str(e)}"
    
    # generate a json file with all of the non empty fields in the TemplateChanges column and a separate json file with all of the non empty fields in the UserVarChanges column
    # Parse template changes into structured format
    structured_template_changes = []
    template_change_map = {}
    for index, row in filtered_df.iterrows():
        if pd.notna(row['TemplateChanges']) and row['TemplateChanges'].strip():
            parsed = parse_template_changes(row['TemplateChanges'], row['Rule'])
            if parsed:
                parsed['Rule'] = row['Rule']
                parsed['ModuleName'] = row['ModuleName']
                structured_template_changes.append(parsed)
                template_change_map[index] = parsed

    existing_change_keys = {(change["Rule"], change["TestName"]) for change in structured_template_changes}

    for _, row in filtered_df.iterrows():
        if pd.notna(row['TemplateChanges']) and row['TemplateChanges'].strip():
            continue
        diffs = _extract_setting_differences(row.get('Explaination'))
        if not diffs:
            continue
        normalized_test_name = row['TestName'].split("::")[-1] if isinstance(row['TestName'], str) else row['TestName']
        key = (row['Rule'], normalized_test_name)
        if key in existing_change_keys:
            continue
        module_text = module_sources.get(row['ModuleName'])
        method = _infer_test_method_from_module(module_text, row['TestName'])
        if not method:
            continue
        parameters = {param: _prepare_template_param_value(value) for param, value in diffs.items()}
        auto_change = {
            "Rule": row['Rule'],
            "Type": "Test",
            "Method": method,
            "TestName": normalized_test_name,
            "Parameters": parameters,
            "ModuleName": row['ModuleName'],
            "Source": "explanation",
        }
        structured_template_changes.append(auto_change)
        existing_change_keys.add(key)
    
    uservar_updates, uservar_change_entries = _collect_uservar_updates(filtered_df, template_change_map)
    # save to the output directory
    output_dir = OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)
    template_changes_path = os.path.join(output_dir, "template_changes.json")
    user_var_changes_path = os.path.join(output_dir, "user_var_changes.json")
    uservar_update_results, applied_uservar_entries = _apply_uservar_updates(output_dir, uservar_updates)

    try:
        with open(template_changes_path, 'w') as f:
            json.dump(structured_template_changes, f, indent=4)
        with open(user_var_changes_path, 'w') as f:
            json.dump(applied_uservar_entries or uservar_change_entries, f, indent=4)
    except Exception as e:
        return f"Error saving changes to JSON files: {str(e)}"
    
    # Update the edited MTPL files with the template changes
    mtpl_update_results = []
    for module_name in unique_modules:
        edited_mtpl_path = os.path.join(output_dir, f"edited_{module_name}.mtpl")
        if os.path.exists(edited_mtpl_path):
            # Filter template changes for this module
            module_changes = [change for change in structured_template_changes 
                            if any(row['ModuleName'] == module_name and row['Rule'] == change['Rule'] 
                                   for _, row in filtered_df.iterrows())]
            
            if module_changes:
                success = update_mtpl_file(edited_mtpl_path, module_changes)
                if success:
                    mtpl_update_results.append(f"✓ Updated {module_name}.mtpl with {len(module_changes)} changes")
                else:
                    mtpl_update_results.append(f"✗ Failed to update {module_name}.mtpl")
            else:
                mtpl_update_results.append(f"- No changes needed for {module_name}.mtpl")
        else:
            mtpl_update_results.append(f"✗ edited_{module_name}.mtpl not found")
    
    # Create detailed summary of changes
    total_changes = len(structured_template_changes)
    rules_processed = [change["Rule"] for change in structured_template_changes]
    unique_modules_updated = len(unique_modules)
    
    # Build change summary
    change_summary = []
    for change in structured_template_changes:
        rule = change["Rule"]
        method = change["Method"]
        test_name = change["TestName"].split("::")[-1] if "::" in change["TestName"] else change["TestName"]
        param_count = len(change["Parameters"])
        change_summary.append(f"  • Rule {rule}: {method} - {test_name} ({param_count} parameters updated)")
    
    response_text = (
        f"🔧 **Thermal Audit MTPL Updates Successfully Applied**\n\n"
        f"**Summary:**\n"
        f"- Rules processed: {', '.join(rules_processed)}\n"
        f"- Total template changes: {total_changes}\n"
        f"- Modules updated: {unique_modules_updated} ({', '.join(unique_modules)})\n\n"
        f"**Template Changes Applied:**\n" + "\n".join(change_summary) + "\n\n"
        f"**Files Generated:**\n"
        f"- `template_changes.json`: Structured template changes in JSON format\n"
        f"- `user_var_changes.json`: User variable changes (if any)\n"
        f"- Original MTPL/UVARS files: Backup copies\n"
        f"- `edited_{{module}}.mtpl`: Updated MTPL files with applied changes\n\n"
        f"**MTPL File Update Results:**\n" + "\n".join(mtpl_update_results) + "\n\n"
        f"If you want me to copy these edits directly into `{tp_path}` (or a different path), just tell me the destination and I’ll handle the replacement while keeping the original filenames.\n\n"
        f"**Detailed Change Log:**\n"
    )

    try:
        detail_table = filtered_df.to_markdown(index=False)
    except ImportError:
        detail_table = filtered_df.to_string(index=False)
    response_text += f"{detail_table}\n\n"

    response_text += (
        f"📁 **Location:** All files saved to `{os.path.abspath(OUTPUT_DIR)}`\n\n"
        f"✅ **Next Steps:**\n"
        f"1. Review the `edited_{{module}}.mtpl` files to verify changes\n"
        f"2. Copy the edited files back to your Test Program's Modules directory\n"
        f"3. Test the updated Test Program to ensure functionality\n"
        f"4. Use the JSON files for audit trail and documentation"
    )
    if uservar_update_results:
        response_text += "\n\n**UserVar Update Results:**\n" + "\n".join(uservar_update_results)
    else:
        response_text += "\n\n**UserVar Update Results:**\n- No uservar changes required"
    return response_text

def parse_template_changes(template_changes_text, rule):
    """Parse template changes text into structured format."""
    if not template_changes_text or pd.isna(template_changes_text):
        return None
    
    try:
        # Split by lines and find the test line
        lines = template_changes_text.strip().split('\n')
        if not lines:
            return None
            
        # Parse the first line: "Test <Method> <TestName>"
        first_line = lines[0].strip()
        if not first_line.startswith('Test '):
            return None
            
        # Extract method and test name
        parts = first_line[5:].split(' ', 1)  # Remove "Test " prefix
        if len(parts) < 2:
            return None
            
        method = parts[0]
        test_name = parts[1]
        
        # Parse parameters from the block between { }
        parameters = {}
        in_block = False
        for line in lines[1:]:
            line = line.strip()
            if line == '{':
                in_block = True
                continue
            elif line == '}':
                break
            elif in_block and '=' in line:
                # Parse parameter line: "key = value;"
                if line.endswith(';'):
                    line = line[:-1]  # Remove semicolon
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                parameters[key] = value
        
        return {
            "Rule": rule,
            "Type": "Test",
            "Method": method,
            "TestName": test_name,
            "Parameters": parameters
        }
    except Exception:
        # If parsing fails, return None
        return None

def update_mtpl_file(mtpl_file_path, structured_template_changes):
    """Update MTPL file with the template changes from structured_template_changes."""
    try:
        # Read the MTPL file
        with open(mtpl_file_path, 'r', encoding='utf-8') as f:
            mtpl_content = f.read()
        
        # Process each template change
        for change in structured_template_changes:
            test_name = change.get("TestName")
            method = change.get("Method")
            parameters = change.get("Parameters", {})
            
            if not test_name or not method or not parameters:
                continue
            
            # Find the test block in the MTPL file
            # Pattern: Test <Method> <TestName>\n{\n...\n}
            import re
            
            # Create pattern to match the test block
            pattern = rf'(Test\s+{re.escape(method)}\s+{re.escape(test_name)}\s*\{{[^}}]*\}})'
            match = re.search(pattern, mtpl_content, re.DOTALL)
            
            if match:
                original_block = match.group(1)
                
                # Parse the existing parameters
                param_pattern = r'\s*(\w+)\s*=\s*([^;]+);'
                existing_params = {}
                param_matches = re.findall(param_pattern, original_block)
                for param_name, param_value in param_matches:
                    existing_params[param_name.strip()] = param_value.strip()
                
                # Update with new parameters
                for param_name, param_value in parameters.items():
                    existing_params[param_name] = param_value
                
                # Reconstruct the test block
                new_block = f"Test {method} {test_name}\n{{\n"
                for param_name, param_value in existing_params.items():
                    # Add quotes if the value doesn't already have them and contains special characters
                    if not (param_value.startswith('"') and param_value.endswith('"')) and ' ' in param_value:
                        param_value = f'"{param_value}"'
                    new_block += f"\t{param_name} = {param_value};\n"
                new_block += "}"
                
                # Replace the original block with the new one
                mtpl_content = mtpl_content.replace(original_block, new_block)
        
        # Write the updated content back to the file
        with open(mtpl_file_path, 'w', encoding='utf-8') as f:
            f.write(mtpl_content)
        
        return True
    except Exception as e:
        print(f"Error updating MTPL file {mtpl_file_path}: {str(e)}")
        return False

# (Removed standalone execution block; tool functions are now exposed via Tools class.)