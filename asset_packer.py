#!/usr/bin/env python
"""
An improved asset packer for the Momentum firmware. This is a modification of the original asset_packer script by @Willy-JL
"""

import pathlib
import shutil
import struct
import typing
import time
import re
import io
import os
import sys
from PIL import Image, ImageOps, ImageFile
import heatshrink2

HELP_MESSAGE = """The Asset packer will convert files to be efficient and compatible with the asset pack system used in Momentum.

Usage :
    \033[32mpython3 asset_packer.py \033[0;33;1mhelp\033[0m
        \033[3mDisplays this message
        \033[0m
    \033[32mpython3 asset_packer.py \033[0;33;1mcreate <Asset Pack Name>\033[0m
        \033[3mCreates a directory with the correct file structure that can be used
        to prepare for the packing process.
        \033[0m
    \033[32mpython3 asset_packer.py \033[0;33;1mconvert <./Asset\\ Pack\\ Directory>\033[0m
        \033[3mConverts all frames to .png files and renames them to the correct format.
        (requires numbers in filenames)
        \033[0m
    \033[32mpython3 asset_packer.py \033[0;33;1mpack <Asset\\ Pack\\ Directory>\033[0m
        \033[3mPacks the specified asset pack into './asset_packs/Asset\\ Pack\\ Name'
        \033[0m
    \033[32mpython3 asset_packer.py \033[0;33;1mpack all\033[0m
        \033[3mPacks all asset packs in the current directory into './asset_packs/'
        \033[0m
    \033[32mpython3 asset_packer.py \033[0;33;1mrecover <./Anim/Path>\033[0m
        \033[3mRecovers png frames from a compiled anim. (currently only works with anims)
        Recovered assets are saved in './recovered/anim\\ name'
        \033[0m
    \033[32mpython3 asset_packer.py\033[0m
        \033[3mSame as 'python3 asset_packer.py pack all'
        (this is to keep compatibility with the original asset_packer.py)
        \033[0m
"""

EXAMPLE_MANIFEST = """Filetype: Flipper Animation Manifest
Version: 1

Name: example_anim
Min butthurt: 0
Max butthurt: 18
Min level: 1
Max level: 30
Weight: 8
"""

EXAMPLE_META = """Filetype: Flipper Animation
Version: 1

Width: 128
Height: 64
Passive frames: 24
Active frames: 0
Frames order: 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23
Active cycles: 0
Frame rate: 1
Duration: 3600
Active cooldown: 0

Bubble slots: 0
"""


def convert_to_bm(img: "Image.Image | pathlib.Path") -> bytes:
    """Converts an image to a bitmap"""
    if not isinstance(img, Image.Image):
        img = Image.open(img)

    with io.BytesIO() as output:
        img = img.convert("1")
        img = ImageOps.invert(img)
        img.save(output, format="XBM")
        xbm = output.getvalue()

    f = io.StringIO(xbm.decode().strip())
    data = f.read().strip().replace("\n", "").replace(" ", "").split("=")[1][:-1]
    data_str = data[1:-1].replace(",", " ").replace("0x", "")
    data_bin = bytearray.fromhex(data_str)

    # compressing the image
    data_encoded_str = heatshrink2.compress(data_bin, window_sz2=8, lookahead_sz2=4)
    data_enc = bytearray(data_encoded_str)
    data_enc = bytearray([len(data_enc) & 0xFF, len(data_enc) >> 8]) + data_enc

    # marking the image as compressed
    if len(data_enc) + 2 < len(data_bin) + 1:
        return b"\x01\x00" + data_enc
    else:
        return b"\x00" + data_bin


def convert_to_bmx(img: "Image.Image | pathlib.Path") -> bytes:
    """Converts an image to a bmx that contains image size info"""
    if not isinstance(img, Image.Image):
        img = Image.open(img)

    data = struct.pack("<II", *img.size)
    data += convert_to_bm(img)
    return data


def recover_png_from_bm(bm: "bytes | pathlib.Path", width: int, height: int) -> Image.Image:
    """Converts a bitmap back to a png (same as convert_to_bm but in reverse)
    The resulting png will not always be the same as the original image as some information is lost during the conversion"""
    if not isinstance(bm, bytes):
        bm = bm.read_bytes()

    # expected_length = (width * height + 7) // 8

    if bm.startswith(b'\x01\x00'):
        data_dec = heatshrink2.decompress(bm[4:], window_sz2=8, lookahead_sz2=4)
    else:
        data_dec = bm[1:]
    
    img = Image.new("1", (width, height))
    img.putdata([1 - ((byte >> i) & 1) for byte in data_dec for i in range(8)])

    return img


def recover_anim_from_bm(src: "pathlib.Path", logger: typing.Callable):
    """Converts a bitmap to a png"""
    if not os.path.exists(src):
        logger(f"\033[31mError: {src} is not a directory\033[0m")
        return

    dst = pathlib.Path("recovered") / src.name
    dst.mkdir(parents=True, exist_ok=True)

    width = 128
    height = 64
    meta = src / "meta.txt"
    if os.path.exists(meta):
        shutil.copyfile(meta, dst / meta.name)
        with open(meta, "r") as f:
            for line in f:
                if line.startswith("Width:"):
                    width = int(line.split(":")[1].strip())
                elif line.startswith("Height:"):
                    height = int(line.split(":")[1].strip())
        logger(f"using width and height from meta.txt")
    else:
        logger(f"meta.txt not found, assuming width={width}, height={height}")

    for file in src.iterdir():
        if file.is_file() and file.suffix == ".bm":
            img = recover_png_from_bm(file.read_bytes(), width, height)
            img.save(dst / file.with_suffix(".png").name)


def copy_file_as_lf(src: "pathlib.Path", dst: "pathlib.Path"):
    """Copy file but replace Windows Line Endings with Unix Line Endings"""
    dst.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))


def pack_anim(src: pathlib.Path, dst: pathlib.Path):
    if not (src / "meta.txt").is_file():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for frame in src.iterdir():
        if not frame.is_file():
            continue
        if frame.name == "meta.txt":
            copy_file_as_lf(frame, dst / frame.name)
        elif frame.name.startswith("frame_"):
            if frame.suffix == ".png":
                (dst / frame.with_suffix(".bm").name).write_bytes(convert_to_bm(frame))
            elif frame.suffix == ".bm":
                if not (dst / frame.name).is_file():
                    shutil.copyfile(frame, dst / frame.name)


def pack_icon_animated(src: pathlib.Path, dst: pathlib.Path):
    if not (src / "frame_rate").is_file() and not (src / "meta").is_file():
        return
    dst.mkdir(parents=True, exist_ok=True)
    frame_count = 0
    frame_rate = None
    size = None
    files = [file for file in src.iterdir() if file.is_file()]
    for frame in sorted(files, key=lambda x: x.name):
        if not frame.is_file():
            continue
        if frame.name == "frame_rate":
            frame_rate = int(frame.read_text().strip())
        elif frame.name == "meta":
            shutil.copyfile(frame, dst / frame.name)
        else:
            dst_frame = dst / f"frame_{frame_count:02}.bm"
            if frame.suffix == ".png":
                if not size:
                    size = Image.open(frame).size
                dst_frame.write_bytes(convert_to_bm(frame))
                frame_count += 1
            elif frame.suffix == ".bm":
                if frame.with_suffix(".png") not in files:
                    shutil.copyfile(frame, dst_frame)
                    frame_count += 1
    if size is not None and frame_rate is not None:
        (dst / "meta").write_bytes(struct.pack("<IIII", *size, frame_rate, frame_count))


def pack_icon_static(src: pathlib.Path, dst: pathlib.Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".png":
        dst.with_suffix(".bmx").write_bytes(convert_to_bmx(src))
    elif src.suffix == ".bmx":
        if not dst.is_file():
            shutil.copyfile(src, dst)


def pack_font(src: pathlib.Path, dst: pathlib.Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".c":
        code = (
            src.read_bytes().split(b' U8G2_FONT_SECTION("')[1].split(b'") =')[1].strip()
        )
        font = b""
        for line in code.splitlines():
            if line.count(b'"') == 2:
                font += (
                    line[line.find(b'"') + 1 : line.rfind(b'"')]
                    .decode("unicode_escape")
                    .encode("latin_1")
                )
        font += b"\0"
        dst.with_suffix(".u8f").write_bytes(font)
    elif src.suffix == ".u8f":
        if not dst.is_file():
            shutil.copyfile(src, dst)


def convert_and_rename_frames(directory: "str | pathlib.Path", logger: typing.Callable):
    """Converts all frames to png and renames them "frame_N.png" (requires the image name to contain the frame number)"""
    already_formatted = True
    for file in directory.iterdir():
        if file.is_file() and file.suffix in (".jpg", ".jpeg", ".png"):
            if not re.match(r"frame_\d+.png", file.name):
                already_formatted = False
                break
    if already_formatted:
        logger(f"\"{directory.name}\" anim is formatted")
        return

    try:
        print(
            f"\033[31mThis will convert all frames for the \"{directory.name}\" anim to png and rename them.\n"
            "This action is irreversible, make sure to back up your files if needed.\n\033[0m"
        )
        input(
            "Press [Enter] if you wish to continue or [Ctrl+C] to cancel"
        )
    except KeyboardInterrupt:
        sys.exit(0)
    print()
    index = 1

    for file in sorted(directory.iterdir(), key=lambda x: x.name):
        # convert it to a png
        if file.is_file() and file.suffix in (".jpg", ".jpeg", ".png"): # i'm sure more can be added here
            filename = file.stem
            if re.search(r"\d+", filename):
                filename = f"frame_{index}.png"
                index += 1
            else:
                filename = f"{filename}.png"

            img = Image.open(file)
            img.save(directory / filename)
            file.unlink()


def convert_and_rename_frames_for_all_anims(directory_for_anims: "str | pathlib.Path", logger: typing.Callable):
    """Converts all frames to png and renames them "frame_N.png for all anims in the given anim folder." (requires the image name to contain the frame number)"""
    for anim in directory_for_anims.iterdir():
        if anim.is_dir():
            convert_and_rename_frames(anim, logger)


def pack_specific(asset_pack_path: "str | pathlib.Path", output_directory: "str | pathlib.Path", logger: typing.Callable):
    """Packs a specific asset pack"""
    asset_pack_path = pathlib.Path(asset_pack_path)
    output_directory = pathlib.Path(output_directory)
    logger(f"Packing asset pack: {asset_pack_path}")

    if not asset_pack_path.is_dir():
        logger(f"\033[31mError: {asset_pack_path} is not a directory\033[0m")
        return
    
    packed = output_directory / asset_pack_path.name

    if packed.exists():
        try:
            if packed.is_dir():
                shutil.rmtree(packed, ignore_errors=True)
            else:
                packed.unlink()
        except Exception:
            logger(f"Failed to remove existing pack: {packed}")
            pass

    # packing anims
    if (asset_pack_path / "Anims/manifest.txt").exists():
        (packed / "Anims").mkdir(parents=True, exist_ok=True) # ensure that the "Anims" directory exists
        copy_file_as_lf(asset_pack_path / "Anims/manifest.txt", packed / "Anims/manifest.txt")
        manifest = (asset_pack_path / "Anims/manifest.txt").read_bytes()

        # Find all the anims in the manifest
        for anim in re.finditer(rb"Name: (.*)", manifest):
            anim = (
                anim.group(1)
                .decode()
                .replace("\\", "/")
                .replace("/", os.sep)
                .replace("\r", "\n")
                .strip()
            )
            logger(f"Compiling anim for pack '{asset_pack_path.name}': {anim}")
            pack_anim(asset_pack_path / "Anims" / anim, packed / "Anims" / anim)

    # packing icons
    if (asset_pack_path / "Icons").is_dir():
        for icons in (asset_pack_path / "Icons").iterdir():
            if not icons.is_dir() or icons.name.startswith("."):
                continue
            for icon in icons.iterdir():
                if icon.name.startswith("."):
                    continue
                if icon.is_dir():
                    logger(f"Compiling icon for pack '{asset_pack_path.name}': {icons.name}/{icon.name}")
                    pack_icon_animated(icon, packed / "Icons" / icons.name / icon.name)
                elif icon.is_file() and icon.suffix in (".png", ".bmx"):
                    logger(f"Compiling icon for pack '{asset_pack_path.name}': {icons.name}/{icon.name}")
                    pack_icon_static(icon, packed / "Icons" / icons.name / icon.name)

    # packing fonts
    if (asset_pack_path / "Fonts").is_dir():
        for font in (asset_pack_path / "Fonts").iterdir():
            if (
                not font.is_file()
                or font.name.startswith(".")
                or font.suffix not in (".c", ".u8f")
            ):
                continue
            logger(f"Compiling font for pack '{asset_pack_path.name}': {font.name}")
            pack_font(font, packed / "Fonts" / font.name)

    logger(f"Finished packing '{asset_pack_path.name}'")
    logger(f"Saved to: {packed}")


def pack_everything(source_directory: "str | pathlib.Path", output_directory: "str | pathlib.Path", logger: typing.Callable):
    """Packs all asset packs in the source directory"""
    try:
        print(
            "\033[31mThis will pack all asset packs in the current directory.\n"
            "The resulting asset packs will be saved to './asset_packs'\n\033[0m"
        )
        input(
            "Press [Enter] if you wish to continue or [Ctrl+C] to cancel"
        )
    except KeyboardInterrupt:
        sys.exit(0)
    print()

    source_directory = pathlib.Path(source_directory)
    output_directory = pathlib.Path(output_directory)

    for source in source_directory.iterdir():
        # Skip folders that are definitely not meant to be packed
        if source == output_directory:
            continue
        if not source.is_dir() or source.name.startswith(".") or source.name in ("venv", "recovered") :
            continue

        pack_specific(source, output_directory, logger)


def create_asset_pack(name: str, output_directory: "str | pathlib.Path", logger: typing.Callable):
    """Creates the file structure for an asset pack"""

    # check for illegal characters
    if not re.match(r"^[a-zA-Z0-9_\- ]+$", name):
        logger(f"\033[31mError: '{name}' contains illegal characters\033[0m")
        return

    if (output_directory / name).exists():
        logger(f"\033[31mError: {output_directory / name} already exists\033[0m")
        return

    # creating a directory with the name of the asset pack
    (output_directory / name).mkdir(parents=True, exist_ok=True)
    # creating subdirectories for the asset pack
    (output_directory / name / "Anims").mkdir(parents=True, exist_ok=True)
    (output_directory / name / "Icons").mkdir(parents=True, exist_ok=True)
    (output_directory / name / "Fonts").mkdir(parents=True, exist_ok=True)
    # creating "manifest.txt" file
    (output_directory / name / "Anims" / "manifest.txt")
    with open(output_directory / name / "Anims" / "manifest.txt", "w") as f:
        f.write(EXAMPLE_MANIFEST)
    # creating an example anim
    (output_directory / name / "Anims" / "example_anim").mkdir(parents=True, exist_ok=True)
    with open(output_directory / name / "Anims" / "example_anim" / "meta.txt", "w") as f:
        f.write(EXAMPLE_META)

    logger(f"Created asset pack '{name}' in '{output_directory}'")

if __name__ == "__main__":
    # for i, arg in enumerate(sys.argv): # debug
    #     print(f"arg {i}: {arg}")
    if len(sys.argv) > 1:
        match sys.argv[1]:
            case "help" | "-h" | "--help":
                print(HELP_MESSAGE)

            case "create":
                if len(sys.argv) == 3:
                    name = " ".join(sys.argv[2:])
                    create_asset_pack(name, pathlib.Path.cwd(), logger=print)

                else:
                    print(HELP_MESSAGE)

            case "pack":
                if len(sys.argv) == 3:
                    if sys.argv[2] == "all":

                        here = pathlib.Path(__file__).absolute().parent
                        start = time.perf_counter()
                        pack_everything(here, here / "asset_packs", logger=print)
                        end = time.perf_counter()
                        print(f"\nFinished in {round(end - start, 2)}s\n")

                    else:
                        pack_specific(sys.argv[2], pathlib.Path.cwd() / "asset_packs", logger=print)
                else:
                    print(HELP_MESSAGE)

            case "convert":
                if len(sys.argv) == 3:
                    convert_and_rename_frames_for_all_anims(pathlib.Path(sys.argv[2]) / "Anims", logger=print)
                else:
                    print(HELP_MESSAGE)

            case "recover":
                if len(sys.argv) == 3:
                    start = time.perf_counter()
                    recover_anim_from_bm(pathlib.Path(sys.argv[2]), logger=print)
                    end = time.perf_counter()
                    print(f"Finished in {round(end - start, 2)}s")

            case _:
                print(HELP_MESSAGE)
    else:
        here = pathlib.Path(__file__).absolute().parent
        start = time.perf_counter()
        pack_everything(here, here / "asset_packs", logger=print)
        end = time.perf_counter()
        print(f"\nFinished in {round(end - start, 2)}s\n")