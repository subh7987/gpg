import os
import re
from pathlib import Path

def find_date_folders(base_path):
    base_path = Path(base_path)
    
    # Agar given path hi date folder hai
    if base_path.is_dir() and re.match(r"^\d{8}$", base_path.name):
        return [base_path]
    
    # Agar base folder hai to uske andar ke date folders scan karo
    date_folders = []
    for item in base_path.iterdir():
        if item.is_dir() and re.match(r"^\d{8}$", item.name):
            date_folders.append(item)
    return date_folders

# Test
base_folder = r"D:\mydata"  # Tum yahan base ya direct date folder de sakte ho
date_folders = find_date_folders(base_folder)

if not date_folders:
    print("❌ No date folders found")
else:
    print("✅ Found date folders:")
    for folder in date_folders:
        print(" -", folder)
