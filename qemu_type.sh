#!/bin/bash
# Type text into QEMU VM via monitor sendkey commands
# Usage: ./qemu_type.sh "text to type"
# Handles special characters that vncdo gets wrong (like colon)

SOCK="/home/dmaynor/code/keyboard-oled-re/vm/qemu-monitor.sock"
TEXT="$1"

send() {
    echo "sendkey $1" | socat - UNIX-CONNECT:"$SOCK" > /dev/null 2>&1
    sleep 0.05
}

for (( i=0; i<${#TEXT}; i++ )); do
    c="${TEXT:$i:1}"
    case "$c" in
        [a-z]) send "$c" ;;
        [A-Z]) send "shift-$(echo "$c" | tr 'A-Z' 'a-z')" ;;
        [0-9]) send "$c" ;;
        ' ')   send "spc" ;;
        ':')   send "shift-semicolon" ;;
        ';')   send "semicolon" ;;
        '.')   send "dot" ;;
        ',')   send "comma" ;;
        '/')   send "slash" ;;
        \\)    send "backslash" ;;
        '-')   send "minus" ;;
        '_')   send "shift-minus" ;;
        '=')   send "equal" ;;
        '+')   send "shift-equal" ;;
        '"')   send "shift-apostrophe" ;;
        "'")   send "apostrophe" ;;
        '%')   send "shift-5" ;;
        '!')   send "shift-1" ;;
        '@')   send "shift-2" ;;
        '#')   send "shift-3" ;;
        '$')   send "shift-4" ;;
        '&')   send "shift-7" ;;
        '*')   send "shift-8" ;;
        '(')   send "shift-9" ;;
        ')')   send "shift-0" ;;
        '[')   send "bracket_left" ;;
        ']')   send "bracket_right" ;;
        '{')   send "shift-bracket_left" ;;
        '}')   send "shift-bracket_right" ;;
        '|')   send "shift-backslash" ;;
        '~')   send "shift-grave_accent" ;;
        '`')   send "grave_accent" ;;
        '<')   send "shift-comma" ;;
        '>')   send "shift-dot" ;;
        '?')   send "shift-slash" ;;
        *)     echo "Unknown char: $c" >&2 ;;
    esac
done
