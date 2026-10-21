#!/usr/bin/env python3
"""
Professional File Renamer
- Renames files in the same folder as this script.
- Sorts files alphabetically by their current filename.
- Keeps each file's extension.
- First file becomes: PREFIX.ext
- Remaining files become: PREFIX1.ext, PREFIX2.ext, ...
- The script itself is excluded.
- Uses temporary names first, preventing collisions with existing names.
"""

from pathlib import Path
import uuid


def main():
    folder = Path(__file__).resolve().parent
    script = Path(__file__).resolve()

    print("=" * 55)
    print("             PROFESSIONAL FILE RENAMER")
    print("=" * 55)
    print(f"\nFolder: {folder}\n")

    prefix = input("Enter the new base filename (example: yori): ").strip()

    if not prefix:
        print("\nERROR: You didn't enter a filename.")
        input("Press Enter to exit...")
        return

    # Remove characters that Windows does not allow in filenames.
    invalid = '<>:"/\\|?*'
    if any(ch in prefix for ch in invalid):
        print('\nERROR: The filename contains invalid Windows characters:')
        print('       < > : " / \\ | ? *')
        input("Press Enter to exit...")
        return

    # Files only, sorted alphabetically. Exclude this Python script.
    files = sorted(
        [p for p in folder.iterdir() if p.is_file() and p.resolve() != script],
        key=lambda p: p.name.lower()
    )

    if not files:
        print("No files found.")
        input("Press Enter to exit...")
        return

    print(f"\nFound {len(files)} file(s).")
    print("\nThey will be renamed in alphabetical order:")
    for i, p in enumerate(files[:10]):
        new_name = f"{prefix}{'' if i == 0 else i}{p.suffix}"
        print(f"  {p.name}  ->  {new_name}")

    if len(files) > 10:
        print(f"  ... and {len(files) - 10} more")

    answer = input("\nContinue? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        print("Cancelled.")
        input("Press Enter to exit...")
        return

    # Phase 1: move everything to unique temporary names.
    # This prevents collisions if target names already exist.
    temporary = []
    try:
        for p in files:
            temp_name = f".__rename_temp_{uuid.uuid4().hex}{p.suffix}"
            temp_path = folder / temp_name
            p.rename(temp_path)
            temporary.append((temp_path, p.suffix))

        # Phase 2: assign final names.
        for i, (temp_path, suffix) in enumerate(temporary):
            new_name = f"{prefix}{'' if i == 0 else i}{suffix}"
            temp_path.rename(folder / new_name)

    except Exception as exc:
        print(f"\nERROR: {exc}")
        print("Some files may have been renamed before the error occurred.")
        input("Press Enter to exit...")
        return

    print("\n" + "=" * 55)
    print(f"SUCCESS! Renamed {len(files)} file(s).")
    print("=" * 55)
    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
