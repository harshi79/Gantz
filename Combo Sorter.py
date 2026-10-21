import os
import re
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from threading import Thread
from collections import defaultdict

class EmailPasswordSorter:
    def __init__(self, root):
        self.root = root
        self.root.title("Email:Password Sorter - Eepy Tool")
        self.root.geometry("900x750")
        self.root.resizable(True, True)
        
        # Colors
        self.bg_color = '#2b2b2b'
        self.root.configure(bg=self.bg_color)
        
        # Style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TLabel', background=self.bg_color, foreground='white')
        style.configure('TButton', background='#3c3f41', foreground='white')
        style.configure('TLabelframe', background=self.bg_color, foreground='white')
        style.configure('TLabelframe.Label', background=self.bg_color, foreground='white')
        
        # Variables
        self.input_folder = tk.StringVar(value="/drops")
        
        # Store data
        self.all_lines = []
        
        self.create_widgets()
        
    def create_widgets(self):
        # Main frame
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Folder selection frame
        file_frame = ttk.LabelFrame(main_frame, text=" 1. Select Source Folder ")
        file_frame.pack(fill=tk.X, pady=5)
        
        folder_select_frame = ttk.Frame(file_frame)
        folder_select_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Entry(folder_select_frame, textvariable=self.input_folder, width=60).pack(side=tk.LEFT, padx=5)
        ttk.Button(folder_select_frame, text="Browse", command=self.browse_folder).pack(side=tk.LEFT, padx=5)
        
        # Load button
        ttk.Button(main_frame, text="Load .txt Files", command=self.load_files, width=30).pack(pady=10)
        
        # Statistics frame
        stats_frame = ttk.LabelFrame(main_frame, text=" Statistics ")
        stats_frame.pack(fill=tk.X, pady=5)
        
        stats_inner = ttk.Frame(stats_frame)
        stats_inner.pack(fill=tk.X, padx=10, pady=10)
        
        self.total_lines_label = ttk.Label(stats_inner, text="Total lines loaded: 0", font=('Arial', 10, 'bold'))
        self.total_lines_label.pack(anchor=tk.W, pady=2)
        
        self.hotmail_lines_label = ttk.Label(stats_inner, text="Hotmail/Outlook/Live lines: 0", font=('Arial', 10, 'bold'))
        self.hotmail_lines_label.pack(anchor=tk.W, pady=2)
        
        self.country_lines_label = ttk.Label(stats_inner, text="Country domains (.de, .uk, etc.): 0", font=('Arial', 10, 'bold'))
        self.country_lines_label.pack(anchor=tk.W, pady=2)
        
        self.other_lines_label = ttk.Label(stats_inner, text="Other domains (mix.txt): 0", font=('Arial', 10, 'bold'))
        self.other_lines_label.pack(anchor=tk.W, pady=2)
        
        # Output info
        output_frame = ttk.LabelFrame(main_frame, text=" Output Files ")
        output_frame.pack(fill=tk.X, pady=5)
        
        output_inner = ttk.Frame(output_frame)
        output_inner.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(output_inner, text="The tool will create:", font=('Arial', 10, 'bold')).pack(anchor=tk.W)
        ttk.Label(output_inner, text="• hotmail.txt - All Hotmail/Outlook/Live/MSN emails", foreground='lightblue').pack(anchor=tk.W, padx=20)
        ttk.Label(output_inner, text="• .de.txt - All .de domain emails", foreground='lightblue').pack(anchor=tk.W, padx=20)
        ttk.Label(output_inner, text="• .uk.txt - All .uk domain emails", foreground='lightblue').pack(anchor=tk.W, padx=20)
        ttk.Label(output_inner, text="• .dl.txt - All .dl domain emails", foreground='lightblue').pack(anchor=tk.W, padx=20)
        ttk.Label(output_inner, text="• [country].txt - For any other country domain found", foreground='lightblue').pack(anchor=tk.W, padx=20)
        ttk.Label(output_inner, text="• mix.txt - All remaining domains (gmail, yahoo, .com, .net, etc.)", foreground='lightblue').pack(anchor=tk.W, padx=20)
        
        self.output_label = ttk.Label(output_inner, text="Output will be saved to: /drops/sorted_output/", foreground='lightgreen')
        self.output_label.pack(anchor=tk.W, pady=10)
        
        # Process button
        ttk.Button(main_frame, text="Process & Sort", command=self.process_files, width=30).pack(pady=10)
        
        # Progress bar
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=5)
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(fill=tk.X, pady=5)
        
        # Log area
        log_frame = ttk.LabelFrame(main_frame, text=" Processing Log ")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        log_inner = ttk.Frame(log_frame)
        log_inner.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_inner, height=15, width=80, 
                                                  bg='#1e1e1e', fg='#d4d4d4', 
                                                  insertbackground='white',
                                                  font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.input_folder.set(folder)
            
    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()
        
    def extract_email_pass_lines(self, content):
        """Extract valid email:password lines, ignore links and malformed lines."""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}:[^\s]+$'
        valid_lines = []
        for line in content.splitlines():
            line = line.strip()
            # Skip lines with http, https, www, or any links
            if re.search(r'http[s]?://|www\.|\.com/|\.net/', line, re.IGNORECASE):
                continue
            # Skip lines without @ or :
            if '@' not in line or ':' not in line:
                continue
            # Check if it matches email:password pattern
            if re.match(pattern, line):
                valid_lines.append(line)
        return valid_lines
    
    def save_with_footer(self, filepath, lines):
        """Save lines with sponsor footer at the beginning."""
        footer = "This Content Was Sponsored By YourCloudName\nYourCloudName\n\n"
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(footer)
            if lines:
                f.write("\n".join(lines))
    
    def load_files(self):
        def load_thread():
            self.progress.start()
            self.status_var.set("Loading files...")
            self.log("=" * 70)
            self.log("STARTING LOAD PROCESS")
            
            input_dir = Path(self.input_folder.get())
            if not input_dir.exists():
                self.log(f"ERROR: Folder {input_dir} not found!")
                messagebox.showerror("Error", f"Folder {input_dir} not found!")
                self.progress.stop()
                self.status_var.set("Error - Folder not found")
                return
            
            self.all_lines = []
            txt_files = list(input_dir.glob("*.txt"))
            
            if not txt_files:
                self.log(f"No .txt files found in {input_dir}")
                messagebox.showwarning("Warning", f"No .txt files found in {input_dir}")
                self.progress.stop()
                self.status_var.set("No files found")
                return
            
            self.log(f"Found {len(txt_files)} .txt files")
            
            for txt_file in txt_files:
                try:
                    with open(txt_file, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        valid_lines = self.extract_email_pass_lines(content)
                        self.all_lines.extend(valid_lines)
                        self.log(f"  ✓ {txt_file.name}: {len(valid_lines)} valid lines")
                except Exception as e:
                    self.log(f"  ✗ Error reading {txt_file.name}: {str(e)}")
            
            self.total_lines_label.config(text=f"Total lines loaded: {len(self.all_lines)}")
            self.log(f"\n✓ TOTAL VALID LINES: {len(self.all_lines)}")
            
            self.progress.stop()
            self.status_var.set(f"Loaded {len(self.all_lines)} lines")
            messagebox.showinfo("Success", f"Loaded {len(self.all_lines)} valid email:password lines!")
            
        Thread(target=load_thread, daemon=True).start()
    
    def process_files(self):
        if not self.all_lines:
            messagebox.showwarning("Warning", "Please load files first!")
            return
        
        def process_thread():
            self.progress.start()
            self.status_var.set("Processing and sorting...")
            self.log("\n" + "=" * 70)
            self.log("STARTING SORTING PROCESS")
            
            input_dir = Path(self.input_folder.get())
            output_folder = input_dir / "sorted_output"
            output_folder.mkdir(exist_ok=True)
            
            self.output_label.config(text=f"Output saved to: {output_folder}")
            self.log(f"\nSaving to: {output_folder}")
            
            # Initialize containers
            hotmail_lines = []
            country_domains = defaultdict(list)  # For .de, .uk, .dl, etc.
            other_lines = []  # For mix.txt
            
            # Sort each line
            for line in self.all_lines:
                email = line.split(':', 1)[0].lower()
                
                # Check for Hotmail/Outlook/Live/MSN
                if any(domain in email for domain in ['@hotmail', '@outlook', '@live', '@msn']):
                    hotmail_lines.append(line)
                # Check for country domains (.de, .uk, .dl, etc.)
                elif email.endswith('.de'):
                    country_domains['de'].append(line)
                elif email.endswith('.uk'):
                    country_domains['uk'].append(line)
                elif email.endswith('.dl'):
                    country_domains['dl'].append(line)
                elif email.endswith('.fr'):
                    country_domains['fr'].append(line)
                elif email.endswith('.it'):
                    country_domains['it'].append(line)
                elif email.endswith('.es'):
                    country_domains['es'].append(line)
                elif email.endswith('.nl'):
                    country_domains['nl'].append(line)
                elif email.endswith('.ru'):
                    country_domains['ru'].append(line)
                elif email.endswith('.br'):
                    country_domains['br'].append(line)
                elif email.endswith('.au'):
                    country_domains['au'].append(line)
                elif email.endswith('.ca'):
                    country_domains['ca'].append(line)
                elif email.endswith('.in'):
                    country_domains['in'].append(line)
                elif email.endswith('.pl'):
                    country_domains['pl'].append(line)
                elif email.endswith('.se'):
                    country_domains['se'].append(line)
                elif email.endswith('.no'):
                    country_domains['no'].append(line)
                elif email.endswith('.dk'):
                    country_domains['dk'].append(line)
                elif email.endswith('.ch'):
                    country_domains['ch'].append(line)
                elif email.endswith('.at'):
                    country_domains['at'].append(line)
                elif email.endswith('.be'):
                    country_domains['be'].append(line)
                else:
                    # Everything else goes to mix.txt
                    other_lines.append(line)
            
            # Update statistics
            self.hotmail_lines_label.config(text=f"Hotmail/Outlook/Live lines: {len(hotmail_lines)}")
            total_country = sum(len(lines) for lines in country_domains.values())
            self.country_lines_label.config(text=f"Country domains (.de, .uk, etc.): {total_country}")
            self.other_lines_label.config(text=f"Other domains (mix.txt): {len(other_lines)}")
            
            # Save hotmail.txt
            if hotmail_lines:
                hotmail_file = output_folder / "hotmail.txt"
                self.save_with_footer(hotmail_file, hotmail_lines)
                self.log(f"✓ hotmail.txt: {len(hotmail_lines)} lines")
            else:
                self.log("⚠ No Hotmail/Outlook/Live lines found")
            
            # Save country domain files
            for country, lines in sorted(country_domains.items()):
                if lines:
                    country_file = output_folder / f".{country}.txt"
                    self.save_with_footer(country_file, lines)
                    self.log(f"✓ .{country}.txt: {len(lines)} lines")
            
            # Save mix.txt (all other domains)
            if other_lines:
                mix_file = output_folder / "mix.txt"
                self.save_with_footer(mix_file, other_lines)
                self.log(f"✓ mix.txt: {len(other_lines)} lines")
            else:
                self.log("⚠ No other domains found for mix.txt")
            
            # Summary
            self.log("\n" + "=" * 70)
            self.log("✓ SORTING COMPLETE!")
            self.log(f"  Total lines processed: {len(self.all_lines)}")
            self.log(f"  Hotmail/Outlook/Live: {len(hotmail_lines)} lines → hotmail.txt")
            self.log(f"  Country domains: {total_country} lines → .de.txt, .uk.txt, etc.")
            self.log(f"  Other domains: {len(other_lines)} lines → mix.txt")
            self.log(f"  Output folder: {output_folder}")
            self.log("\n" + "=" * 70)
            self.log("This Content Was Sponsored By YourCloudName")
            self.log("YourCloudLink")
            
            self.progress.stop()
            self.status_var.set("Complete! Files created successfully")
            messagebox.showinfo("Complete", f"Successfully sorted all emails!\n\nOutput folder: {output_folder}\n\nFiles created:\n• hotmail.txt\n• .de.txt, .uk.txt, etc.\n• mix.txt")
            
        Thread(target=process_thread, daemon=True).start()

def main():
    root = tk.Tk()
    app = EmailPasswordSorter(root)
    root.mainloop()

if __name__ == "__main__":
    main()