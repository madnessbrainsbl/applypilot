"""Install the free browser and local audio model; never submits applications."""
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def repair_windows_manifest(executable):
    original = executable.read_bytes()
    pattern = rb'<dependency>\s*<dependentAssembly>\s*<assemblyIdentity\s+type="win32"\s+name="mozglue"\s+version="1\.0\.0\.0"\s+language="\*"\s*/>\s*</dependentAssembly>\s*</dependency>'
    matches = list(re.finditer(pattern, original))
    if not matches:
        return False
    if len(matches) != 1:
        raise RuntimeError('Unexpected mozglue manifest; executable left unchanged')
    backup = executable.with_suffix('.exe.before-sxs-fix')
    if backup.exists():
        if backup.read_bytes() != original:
            raise RuntimeError('Existing executable backup differs; left unchanged')
    else:
        shutil.copy2(executable, backup)
    match = matches[0]
    # shortcut: Windows build #650 has this invalid private-DLL dependency; remove after upstream fixes it.
    replacement = b'<file name="mozglue.dll" />'.ljust(match.end() - match.start(), b' ')
    executable.write_bytes(original[:match.start()] + replacement + original[match.end():])
    return True


def main():
    from camoufox.pkgman import CamoufoxFetcher, launch_path, list_available_versions
    from huggingface_hub import snapshot_download
    version = next(v for v in list_available_versions() if v.version.full_string == '156.0.1-beta.36')
    CamoufoxFetcher(selected_version=version).install()
    executable = Path(launch_path())
    patched = repair_windows_manifest(executable) if sys.platform == 'win32' else False
    snapshot_download('Systran/faster-whisper-base.en', local_dir=ROOT / 'autopilot/cache/whisper-base.en',
                      revision='3d3d5dee26484f91867d81cb899cfcf72b96be6c',
                      allow_patterns=['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'])
    print(json.dumps({'executable': str(executable), 'windows_manifest_repaired': patched,
                      'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                      'audio_model': 'Systran/faster-whisper-base.en'}))


if __name__ == '__main__':
    main()
