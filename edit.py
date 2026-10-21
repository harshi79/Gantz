from pathlib import Path

TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".log", ".json", ".xml",
    ".html", ".htm", ".ini", ".cfg", ".conf",
    ".yaml", ".yml"
}


def read_file(path):
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            pass

    raise ValueError("Could not read this file as text")


def main():
    folder = Path(__file__).resolve().parent
    script = Path(__file__).resolve()

    print("=" * 60)
    print("                 FILE EDITOR")
    print("=" * 60)
    print(f"\nFolder: {folder}")

    # 1. Ask what to search for
    search = input("\nEnter the link/text to search for: ")

    if not search:
        print("Search text cannot be empty.")
        input("Press Enter to exit...")
        return

    # Find all supported files
    files = sorted(
        [
            p for p in folder.iterdir()
            if p.is_file()
            and p.resolve() != script
            and p.suffix.lower() in TEXT_EXTENSIONS
        ],
        key=lambda p: p.name.lower()
    )

    print(f"\nScanning {len(files)} file(s)...")

    matches = []

    # 2. Search every file
    for file in files:
        try:
            content, encoding = read_file(file)
            count = content.count(search)

            if count:
                matches.append((file, count, encoding))

        except Exception as e:
            print(f"[SKIP] {file.name}: {e}")

    total = sum(count for _, count, _ in matches)

    # 3. Show results
    print("\n" + "-" * 60)
    print("SEARCH RESULT")
    print("-" * 60)
    print(f"Files containing it : {len(matches)}")
    print(f"Total occurrences   : {total}")

    if not matches:
        print("\nNothing found.")
        input("Press Enter to exit...")
        return

    print("\nMatching files:")
    for file, count, _ in matches:
        print(f"  {file.name} ({count})")

    # 4. Ask what to do
    print("\n" + "-" * 60)
    print("What do you want to do?")
    print("1. Direct edit - DELETE the found text/link")
    print("2. Replace     - replace it with something else")
    print("-" * 60)

    choice = input("Choose [1/2]: ").strip()

    if choice == "1":
        replacement = ""
        action = "DELETE"

    elif choice == "2":
        replacement = input("\nEnter replacement text: ")
        action = "REPLACE"

    else:
        print("\nInvalid choice.")
        input("Press Enter to exit...")
        return

    # 5. Confirmation
    print("\n" + "-" * 60)

    if action == "DELETE":
        print(f"Direct edit: {total} occurrence(s) will be deleted.")

    else:
        print(f"Replace with: {replacement!r}")

    confirm = input("Proceed? [y/N]: ").strip().lower()

    if confirm not in ("y", "yes"):
        print("Cancelled. No files changed.")
        input("Press Enter to exit...")
        return

    # 6. Edit every matching file
    changed = 0
    occurrences = 0

    print("\nEditing...")

    for file, _, encoding in matches:
        try:
            content, _ = read_file(file)

            count = content.count(search)

            if count:
                new_content = content.replace(search, replacement)

                # Directly overwrite the existing file
                file.write_text(new_content, encoding=encoding)

                changed += 1
                occurrences += count

                print(f"[EDITED] {file.name} ({count})")

        except Exception as e:
            print(f"[ERROR] {file.name}: {e}")

    # 7. Summary
    print("\n" + "=" * 60)
    print("                    COMPLETE")
    print("=" * 60)
    print(f"Files changed : {changed}")
    print(f"Occurrences   : {occurrences}")

    # 8. Ask if another search should be performed
    again = input("\nSearch for another link/text? [Y/n]: ").strip().lower()

    if again not in ("n", "no"):
        print()
        main()


if __name__ == "__main__":
    main()