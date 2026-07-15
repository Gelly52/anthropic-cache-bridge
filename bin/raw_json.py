"""Helpers for locating and editing values without re-serializing JSON."""

import json


DECODER = json.JSONDecoder()


def skip_ws(text, pos):
    while pos < len(text) and text[pos] in " \t\n\r":
        pos += 1
    return pos


def value_end(text, pos):
    pos = skip_ws(text, pos)
    try:
        _, end = DECODER.raw_decode(text, pos)
        return end
    except json.JSONDecodeError:
        return -1


def find_key_value_pos(text, object_pos, key):
    pos = object_pos + 1
    while pos < len(text):
        pos = skip_ws(text, pos)
        if pos >= len(text) or text[pos] == "}":
            break
        try:
            key_value, key_end = DECODER.raw_decode(text, pos)
        except json.JSONDecodeError:
            break
        pos = skip_ws(text, key_end)
        if pos >= len(text) or text[pos] != ":":
            break
        value_start = skip_ws(text, pos + 1)
        end = value_end(text, value_start)
        if end < 0:
            break
        if key_value == key:
            return value_start, end
        pos = skip_ws(text, end)
        if pos < len(text) and text[pos] == ",":
            pos += 1
    return None, None


def find_array_element_pos(text, array_pos, index):
    pos = array_pos + 1
    current = 0
    while pos < len(text):
        pos = skip_ws(text, pos)
        if pos >= len(text) or text[pos] == "]":
            break
        element_start = pos
        element_end = value_end(text, element_start)
        if element_end < 0:
            break
        if current == index:
            return element_start, element_end
        pos = skip_ws(text, element_end)
        if pos < len(text) and text[pos] == ",":
            pos += 1
        current += 1
    return None, None


def apply_edits(text, edits):
    result = text
    for start, end, replacement in sorted(edits, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result
