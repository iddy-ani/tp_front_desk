"""
requirements: pandas,pymongo
"""

import traceback
import time
import os
import json
import pandas as pd
from typing import Optional

import pymongo
from pydantic import BaseModel, Field

# NOTE: Removed hardcoded credentials. Provide Mongo URI via either environment variable
# THERMAL_AUDIT_MONGO_URI or through tool Valves (mongo_uri). Database name defaults to axel_egpt
# but can be overridden via Valves.

SUBMODULE_NAME = "thermal_audit"


# MongoDB connection URIs
mongo_uri_axel_egpt = 'mongodb://axel_egpt_rw:eRmVhUp05SfSnXm@p1ir1mon019.ger.corp.intel.com:7177,p2ir1mon019.ger.corp.intel.com:7177,p3ir1mon019.ger.corp.intel.com:7177/axel_egpt?ssl=true&replicaSet=mongo7177' 
mongo_uri_thermal = 'mongodb://Thermals_ro:91g38XuEnZzWk7e@p1ir1mon019.ger.corp.intel.com:7177,p2ir1mon019.ger.corp.intel.com:7177,p3ir1mon019.ger.corp.intel.com:7177/Thermals?ssl=true&replicaSet=mongo7177'

# Database names
axel_egpt_db_name = "axel_egpt"
thermal_db_name = "Thermals"

def _get_mongo_client(mongo_uri: str) -> pymongo.MongoClient:
    """Get MongoDB client with error handling."""
    try:
        return pymongo.MongoClient(mongo_uri)
    except Exception as e:
        raise RuntimeError(f"Failed to connect to MongoDB: {e}")

def _get_collections():
    """Get MongoDB collections for thermal audit operations."""
    thermal_client = _get_mongo_client(mongo_uri_thermal)
    axel_client = _get_mongo_client(mongo_uri_axel_egpt)
    
    thermal_db = thermal_client[thermal_db_name]
    axel_db = axel_client[axel_egpt_db_name]
    
    thermal_results_collection = thermal_db['Results']
    tat_results_collection = axel_db['thermalAuditResults']
    
    return thermal_results_collection, tat_results_collection

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

def transform_results(thermal_result):
    """Transform thermal results into desired format."""
    transformed = {}
    for key, value in thermal_result.items():
        if key == "Items" and isinstance(value, list):
            transformed_items = []
            for item in value:
                transformed_item = {
                    "Rule": item.get("Rule"),
                    "ModuleName": item.get("ModuleName"),
                    "TestName": item.get("TestName"),
                    "Valid": item.get("Valid"),
                    "Explaination": item.get("Explaination"),
                    "TemplateChanges": item.get("TemplateChanges"),
                    "UserVarChanges": item.get("UserVarChanges"),
                }
                transformed_items.append(transformed_item)
            transformed[key] = transformed_items
        else:
            transformed[key] = value
    return transformed

def check_thermal_audit_completion_status(tp_name: str) -> str:
    """Check the completion status of a thermal audit job."""
    try:
        thermal_results_collection, tat_results_collection = _get_collections()
        
        document = thermal_results_collection.find_one({"TestProgram": tp_name})
        if not document:
            return f"No thermal results found for Test Program '{tp_name}'. I can submit a Thermal audit job for you or you can contact axel_adtl@intel.com for assistance."
        
        status = document.get("Status", "Not_Started")
        if status == 'Not_Started':
            return f"Thermal Audit has not yet completed. If you have submitted a Thermal Audit job and more than 5 minutes have passed please contact axel_adtl@intel.com for assistance. Otherwise, I can submit a Thermal audit job for you."
        elif status == "Error":
            error_text = document.get('ErrorMessage', 'Unknown error')
            return f"There was an error with the processing of the Thermal Audit for {tp_name}. Please verify the inputs or contact axel_adtl@intel.com for assistance. Error: {error_text}"

        # Status is Completed, get results
        clean_results = _perform_thermal_audit(tp_name=tp_name)
        return f'Thermal Audit completed: {clean_results}'
    except Exception as ex:
        return f"Exception occurred: {str(ex)}"

def submit_tp_for_audit(tp_path: str, tp_name: str, poll_interval: int, max_wait: int, wait: bool = False) -> str:
    """Submit a test program for thermal audit."""
    try:
        thermal_results_collection, tat_results_collection = _get_collections()
        
        payload = {
            "TP_Path": tp_path,
            "TestProgram": tp_name,
            "Module": "ThermalAudit",
            "Status": "Not_Started"
        }
        result = thermal_results_collection.insert_one(payload)
        
        if wait:
            start_time = time.monotonic()
            status = "Not_Started"
            while True:
                document = thermal_results_collection.find_one({"_id": result.inserted_id})
                if document:
                    status = document.get("Status", "Not_Started")
                    if status in ["Completed", "Error"]:
                        break
                if time.monotonic() - start_time > max_wait:
                    return f"Timeout waiting for audit completion after {max_wait}s. Last status: {status}. The API may be down, please contact axel_adtl@intel.com for assistance"
                time.sleep(poll_interval)

            if status == "Error":
                return f"Error in Thermal Audit submodule: {document.get('ErrorMessage', 'Unknown error')}"

            # Status is Completed, get results
            clean_results = _perform_thermal_audit(tp_name=tp_name)
            return f'Thermal Audit submitted and completed: {clean_results}'
        else:
            return f'Thermal Audit for {tp_name} has been submitted successfully. RunID: {result.inserted_id}'
    except Exception as ex:
        return f"Exception occurred: {str(ex)}"

def _perform_thermal_audit(tp_name: str) -> str:
    """Process thermal audit results and generate CSV/markdown output."""
    try:
        thermal_results_collection, tat_results_collection = _get_collections()
        
        # Find thermal result by TestProgram name
        thermal_result = thermal_results_collection.find_one({"TestProgram": tp_name})
        if not thermal_result:
            return f"No thermal results found for Test Program '{tp_name}'. I can submit a Thermal audit job for you or you can contact axel_adtl@intel.com for assistance."
        
        results = transform_results(thermal_result)
        # store in tat_results_collection
        # tat_results_collection.insert_one(results)

        cleaned_results = clean_data(results)
        failing_items_dict = filter_failing_items(cleaned_results)

        output_dir = os.path.join("Outputs", "ThermalAuditResults")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{tp_name}.csv")

        items = cleaned_results.get("Items")
        open_tat_link = cleaned_results.get("OpenTATWebLink")
        if isinstance(items, list) and all(isinstance(i, dict) for i in items):
            for item in items:
                if open_tat_link is not None:
                    item["OpenTATWebLink"] = open_tat_link
            df = pd.DataFrame(items)
            df.to_csv(output_path, index=False)
        else:
            df = pd.DataFrame([cleaned_results])
            df.to_csv(output_path, index=False)

        results_for_table = dict(failing_items_dict)
        results_for_table.pop("OpenTATWebLink", None)
        if results_for_table:
            table_header = "| Key | Value |\n|---|---|\n"
            table_rows = "\n".join([f"| {key} | {value} |" for key, value in results_for_table.items()])
            markdown_table = table_header + table_rows
        else:
            markdown_table = "No failing items identified."

        response_lines = []
        if open_tat_link:
            response_lines.append(f"You can view the audit results at this link: https://axel.app.intel.com/thermalAuditResult/{tp_name}")
        response_lines.append(f"Results saved to: {output_path}.")
        response_lines.append("Results (failing items focus):\n" + markdown_table)
        response_text = "\n\n".join(response_lines)
        return response_text
    except Exception as e:
        return f"Error processing thermal audit results: {str(e)}"

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

    def thermal_audit(self, tp_name: str, __user__={}, __id__="") -> str:
        """Run the thermal audit against the specified Test Program path.

        :param tp_name: Name/identifier of the test program.
        :return: String with results or error message.
        """
        try:
            if not tp_name:
                return "tp_name is required."
            
            # Check if results already exist
            status_result = check_thermal_audit_completion_status(tp_name)
            if status_result:
                return status_result
        except Exception:
            return f"Error in Thermal Audit submodule: {str(traceback.format_exc())}"
    
    def apply_mtpl_updates(self, tp_path: str, rules: str = "", __user__={}, __id__="") -> str:
        """Apply MTPL / UserVar updates based on prior audit results.

        :param tp_path: Test program root path.
        :param rules: Comma-separated rule IDs to apply (without trailing 'p').
        :return: String with results or error message.
        """
        arguments = {"tp_path": tp_path, "rules": rules}
        return update_mtpl_usrv(arguments)
    
    def check_completion_status(self, tp_name: str, __user__={}, __id__="") -> str:
        """Check the completion status of a thermal audit job.

        :param tp_name: Name/identifier of the test program to check status for.
        :return: String with status information or error message.
        """
        try:
            if not tp_name:
                return "tp_name is required."
            return check_thermal_audit_completion_status(tp_name)
        except Exception:
            return f"Error checking thermal audit status: {str(traceback.format_exc())}"
    
    def submit_for_audit(self, tp_path: str, tp_name: str, wait: bool = True, __user__={}, __id__="") -> str:
        """Submit a test program for thermal audit.

        :param tp_path: Full path to the test program directory (network drive).
        :param tp_name: Name/identifier of the test program.
        :param wait: Whether to wait for completion (default True) or just submit and return immediately.
        :return: String with submission results or error message.
        """
        try:
            if not tp_name:
                return "tp_name is required."
            if not tp_path:
                return "tp_path is required."
            
            poll_interval = getattr(self, 'valves', None) and self.valves.poll_interval_seconds or 5
            max_wait = getattr(self, 'valves', None) and self.valves.max_wait_seconds or 600
            return submit_tp_for_audit(tp_path, tp_name, poll_interval, max_wait, wait)
        except Exception:
            return f"Error submitting thermal audit: {str(traceback.format_exc())}"

def update_mtpl_usrv(arguments: dict) -> str:
    tp_path = arguments.get("tp_path", '')
    rules_str = arguments.get("rules", "")
    rules_list = [rule.strip() + "p" for rule in rules_str.split(",") if rule.strip()] if rules_str else []
    # check if rules_list is empty, if it is then we will return a message saying please provide rules to filter the results.
    if not rules_list:
        return "Please provide rules to filter the results."
    # ensure tp_path is a valid directory
    if not os.path.isdir(tp_path):
        return f"The provided path '{tp_path}' is not a valid directory."
    # check if a folder named "Outputs/ThermalAuditResults" in the current working dir, not in tp_path, and if there is a csv file in there. If there are multiple files then we will need to return a message saying multiple files found please remove all but 1.
    thermal_audit_result_dir = os.path.join("Outputs", "ThermalAuditResults")
    # ensure the directory exists
    if not os.path.exists(thermal_audit_result_dir):
        return "No thermal audit results found. Please run the thermal audit submodule first."
    csv_files = [f for f in os.listdir(thermal_audit_result_dir) if f.endswith('.csv')]
    if len(csv_files) == 0:
        return "No thermal audit results found. Please run the thermal audit submodule first."
    elif len(csv_files) > 1:
        return "Multiple thermal audit results found. Please remove all but one CSV file from the thermal_audit_result directory."
    csv_file_path = os.path.join(thermal_audit_result_dir, csv_files[0])
    # Read the CSV file
    try:
        df = pd.read_csv(csv_file_path)
    except Exception as e:
        return f"Error reading the CSV file: {str(e)}"
    # Check if the DataFrame is empty
    if df.empty:
        return "The CSV file is empty. Please run the thermal audit submodule first."
    # Filter the DataFrame based on the rules
    if rules_list:
        filtered_df = df[df['Rule'].isin(rules_list)]
        # only keep the Rule, Module, updatedtest and updatedrules columns
        filtered_df = filtered_df[['Rule', 'ModuleName', 'TestName', 'TemplateChanges', 'UserVarChanges']]
        if filtered_df.empty:
            return "No matching rules found in the thermal audit results."
    
    # get the unique ModuleNames from the filtered DataFrame
    unique_modules = filtered_df['ModuleName'].unique()
        
    # in the tp_path navigate to Modules\<ModuleName> dir and copy the <ModuleName>.mtpl and <ModuleName>.usrv files to the Outputs/ThermalAuditResults directory
    for module_name in unique_modules:
        module_path = os.path.join(tp_path, "Modules", module_name)
        if not os.path.exists(module_path):
            return f"Module '{module_name}' does not exist in the Test Program directory."
        mtpl_file = os.path.join(module_path, f"{module_name}.mtpl")
        if not os.path.exists(mtpl_file):
            return f"Required mtpl file for module '{module_name}' is missing."
        usrv_file = os.path.join(module_path, f"{module_name}.usrv")
        is_usrv_file = os.path.exists(usrv_file)
        if not is_usrv_file:
            note = f"Required usrv file for module '{module_name}' is missing. This may not be an issue if the module does not use user variables."
        # robocopy the files to the Outputs/ThermalAuditResults directory. they are not csv they are custom files closer to json or dict format
        try:
            import shutil
            output_dir = os.path.join("Outputs", "ThermalAuditResults")
            os.makedirs(output_dir, exist_ok=True)
            mtpl_dest = os.path.join(output_dir, f"{module_name}.mtpl")
            # Copy the files
            shutil.copy2(mtpl_file, mtpl_dest)
            # Now make a copy of the files and call it edited_<ModuleName>.mtpl and edited_<ModuleName>.usrv
            local_mtpl_file = os.path.join(output_dir, f"{module_name}.mtpl")
            edited_mtpl_dest = os.path.join(output_dir, f"edited_{module_name}.mtpl")
            shutil.copy2(local_mtpl_file, edited_mtpl_dest)
            # If usrv_file exists, copy it as well
            if is_usrv_file:
                usrv_dest = os.path.join(output_dir, f"{module_name}.usrv")
                shutil.copy2(usrv_file, usrv_dest)
                local_usrv_file = os.path.join(output_dir, f"{module_name}.usrv")
                edited_usrv_dest = os.path.join(output_dir, f"edited_{module_name}.usrv")
                shutil.copy2(local_usrv_file, edited_usrv_dest)   
        except Exception as e:
            return f"Error copying files for module '{module_name}': {str(e)}"
    
    # generate a json file with all of the non empty fields in the TemplateChanges column and a separate json file with all of the non empty fields in the UserVarChanges column
    # Parse template changes into structured format
    structured_template_changes = []
    for index, row in filtered_df.iterrows():
        if pd.notna(row['TemplateChanges']) and row['TemplateChanges'].strip():
            parsed = parse_template_changes(row['TemplateChanges'], row['Rule'])
            if parsed:
                structured_template_changes.append(parsed)
    
    user_var_changes = filtered_df['UserVarChanges'].dropna().to_dict()
    # save to the output directory
    output_dir = os.path.join("Outputs", "ThermalAuditResults")
    os.makedirs(output_dir, exist_ok=True)
    template_changes_path = os.path.join(output_dir, "template_changes.json")
    user_var_changes_path = os.path.join(output_dir, "user_var_changes.json")
    try:
        with open(template_changes_path, 'w') as f:
            json.dump(structured_template_changes, f, indent=4)
        with open(user_var_changes_path, 'w') as f:
            json.dump(user_var_changes, f, indent=4)
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
        f"**Detailed Change Log:**\n"
        f"{filtered_df.to_markdown(index=False)}\n\n"
        f"📁 **Location:** All files saved to `Outputs/ThermalAuditResults/`\n\n"
        f"✅ **Next Steps:**\n"
        f"1. Review the `edited_{{module}}.mtpl` files to verify changes\n"
        f"2. Copy the edited files back to your Test Program's Modules directory\n"
        f"3. Test the updated Test Program to ensure functionality\n"
        f"4. Use the JSON files for audit trail and documentation"
    )
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