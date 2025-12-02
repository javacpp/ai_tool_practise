import os
import sys
import json
import argparse
import re
from typing import List, Dict, Union, Optional
from pypdf import PdfReader, PdfWriter

def sanitize_filename(filename: str) -> str:
    """
    Removes invalid characters from a filename and replaces spaces with underscores.
    """
    # Replace invalid characters with underscore
    s = re.sub(r'[\\/*?:",<>|]', "_", filename)
    # Replace spaces with underscores
    s = s.replace(" ", "_")
    return s.strip()

def get_destination_page_number(item, reader: PdfReader) -> Optional[int]:
    """
    Safely extracts the page number from an outline item.
    """
    try:
        return reader.get_destination_page_number(item)
    except Exception:
        # Sometimes items might be broken or point to external links
        return None

def get_split_nodes(outline: List[Union[Dict, List]], reader: PdfReader, current_depth: int, target_depth: int) -> List[Dict]:
    """
    Recursively extracts nodes that should serve as split points.
    
    Logic:
    - If we are at the target depth, we take the current node.
    - If we are above the target depth:
        - If the node has children, we recurse into the children.
        - If the node has NO children (it's a leaf node at a higher level, e.g. 'Preface'), 
          we must take it, otherwise this content would be skipped.
    """
    nodes = []
    
    for item in outline:
        if isinstance(item, list):
            # This is a list of children for the previous item. 
            # In pypdf, the outline is a flat list where children are nested lists.
            # However, the structure is: [Item1, [Child1, Child2], Item2, ...]
            # But pypdf's `reader.outline` returns a list where children are nested.
            # Let's handle the recursion carefully.
            # Actually, pypdf outline structure:
            # [ Destination1, [Destination1.1, Destination1.2], Destination2 ]
            # The list immediately following a Destination is its children.
            pass 
        else:
            # It's a Destination object
            title = item.title
            page_num = get_destination_page_number(item, reader)
            
            if page_num is None:
                continue

            # Check if this item has children
            # In pypdf, the children are in the NEXT element if it is a list
            # We need to look ahead in the main loop, but this function receives the whole list.
            # Let's iterate with index to look ahead.
            pass

    # Re-implementing with index iteration to handle the pypdf structure correctly
    i = 0
    while i < len(outline):
        item = outline[i]
        
        # If it's a list, it's children of the PREVIOUS item, which we already handled.
        # We should have handled the recursion when processing the parent.
        # Wait, pypdf structure is: [Parent1, [Child1, Child2], Parent2]
        
        if isinstance(item, list):
            # We shouldn't hit a list as a primary item in this loop if we handle lookahead correctly,
            # UNLESS the outline starts with a list (unlikely) or we recurse into it.
            # If we are recursing, `outline` IS the list of children.
            # So we just recurse.
            nodes.extend(get_split_nodes(item, reader, current_depth + 1, target_depth))
            i += 1
            continue
            
        # It is a Destination (Parent)
        title = item.title
        page_num = get_destination_page_number(item, reader)
        
        has_children = False
        children = []
        if i + 1 < len(outline) and isinstance(outline[i+1], list):
            has_children = True
            children = outline[i+1]
        
        # DECISION LOGIC
        if current_depth == target_depth:
            # We are at the target layer. We take this node.
            # We do NOT recurse deeper even if it has children, because we want to split at this level.
            if page_num is not None:
                nodes.append({"title": title, "page": page_num, "level": current_depth})
        
        elif current_depth < target_depth:
            # We are above the target layer.
            if has_children:
                # Recurse into children to find target layer nodes
                # The children list is at outline[i+1]
                # We process the children list which effectively increases depth
                # Note: The children list is just a list of items, so we pass it to get_split_nodes
                # with depth + 1
                child_nodes = get_split_nodes(children, reader, current_depth + 1, target_depth)
                nodes.extend(child_nodes)
            else:
                # It's a leaf node above target layer (e.g. Preface).
                # We must keep it.
                if page_num is not None:
                    nodes.append({"title": title, "page": page_num, "level": current_depth})
        
        # If current_depth > target_depth, we shouldn't be here if logic is correct, 
        # unless we started deeper? (Not possible with default start=1)
        
        # If we processed children, skip the next item in the main loop
        if has_children:
            i += 2
        else:
            i += 1
            
    return nodes

def generate_split_plan(pdf_path: str, layer: int, prefix: Optional[str] = None) -> List[Dict]:
    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        print(f"Error opening PDF: {e}")
        return []

    if not reader.outline:
        print("PDF has no outline.")
        return []

    # Determine prefix
    if not prefix:
        # Use filename as default prefix
        base_name = os.path.basename(pdf_path)
        name_without_ext = os.path.splitext(base_name)[0]
        # Sanitize and limit to 10 words
        clean_name = sanitize_filename(name_without_ext)
        words = clean_name.split('_')
        if len(words) > 10:
            words = words[:10]
        prefix = "_".join(words)
    else:
        prefix = sanitize_filename(prefix)

    # Extract nodes of interest
    # Root level is depth 1
    raw_nodes = get_split_nodes(reader.outline, reader, 1, layer)
    
    if not raw_nodes:
        print("No matching outline items found.")
        return []

    # Sort nodes by page number to ensure correct order
    # (Outlines are usually ordered, but good to be safe)
    raw_nodes.sort(key=lambda x: x['page'])

    plan = []
    total_pages = len(reader.pages)

    for i, node in enumerate(raw_nodes):
        start_page = node['page']
        title = node['title']
        
        # Determine end page
        if i + 1 < len(raw_nodes):
            next_start = raw_nodes[i+1]['page']
            end_page = next_start
        else:
            end_page = total_pages

        # Sanity check
        if start_page >= end_page:
            print(f"Warning: Section '{title}' starts and ends on same page ({start_page}). Skipping or merging.")
            continue

        # Generate filename with prefix and underscores
        sanitized_title = sanitize_filename(title)
        filename = f"{prefix}_{i+1:02d}_{sanitized_title}.pdf"
        
        plan.append({
            "title": title,
            "start_page": start_page,
            "end_page": end_page,
            "filename": filename
        })

    return plan

def execute_split(pdf_path: str, plan: List[Dict], output_dir: str):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    reader = PdfReader(pdf_path)
    
    for item in plan:
        start = item['start_page']
        end = item['end_page']
        fname = item['filename']
        
        writer = PdfWriter()
        for p in range(start, end):
            writer.add_page(reader.pages[p])
        
        out_path = os.path.join(output_dir, fname)
        with open(out_path, "wb") as f:
            writer.write(f)
        print(f"Created: {out_path}")

def main():
    parser = argparse.ArgumentParser(description="Split PDF based on outline layer.")
    parser.add_argument("pdf_path", help="Path to source PDF")
    parser.add_argument("output_dir", help="Directory to save split files")
    parser.add_argument("--layer", type=int, default=1, help="Outline depth to split at (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Only generate plan, do not split")
    parser.add_argument("--plan-file", help="Path to save/load JSON plan")
    parser.add_argument("--prefix", help="Custom prefix for generated files (default: derived from filename)")
    
    args = parser.parse_args()

    # 1. Generate Plan
    print(f"Analyzing PDF outline at layer {args.layer}...")
    plan = generate_split_plan(args.pdf_path, args.layer, args.prefix)
    
    if not plan:
        print("Could not generate a valid split plan.")
        sys.exit(1)

    # Output plan info
    print(f"Found {len(plan)} sections.")
    for p in plan:
        print(f"  - {p['filename']}: Pages {p['start_page']} to {p['end_page']}")

    # Save plan if requested
    if args.plan_file:
        with open(args.plan_file, 'w') as f:
            json.dump(plan, f, indent=2)
        print(f"Plan saved to {args.plan_file}")

    # 2. Execute Split (unless dry run)
    if not args.dry_run:
        print("\nExecuting split...")
        execute_split(args.pdf_path, plan, args.output_dir)
        print("Done.")
    else:
        print("\nDry run complete. No files created.")

if __name__ == "__main__":
    main()
