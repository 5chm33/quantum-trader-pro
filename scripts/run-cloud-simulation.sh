#!/usr/bin/env bash
set -euo pipefail

umask 077

: "${QTP_DATA_FILE:?QTP_DATA_FILE must point to a readable local OHLCV CSV}"

QTP_STATE_ROOT="${QTP_STATE_ROOT:-/var/lib/quantum-trader}"
QTP_SYMBOL="${QTP_SYMBOL:-DEMO}"
QTP_INITIAL_CASH="${QTP_INITIAL_CASH:-100000}"
QTP_FAST_WINDOW="${QTP_FAST_WINDOW:-50}"
QTP_SLOW_WINDOW="${QTP_SLOW_WINDOW:-200}"
QTP_INVESTED_FRACTION="${QTP_INVESTED_FRACTION:-0.95}"
QTP_SLIPPAGE_BPS="${QTP_SLIPPAGE_BPS:-2}"
QTP_FEE_PER_ORDER="${QTP_FEE_PER_ORDER:-0}"
QTP_FEE_PER_SHARE="${QTP_FEE_PER_SHARE:-0.005}"
QTP_MAX_POSITION_FRACTION="${QTP_MAX_POSITION_FRACTION:-0.95}"
QTP_MAX_ORDER_NOTIONAL="${QTP_MAX_ORDER_NOTIONAL:-1000000}"
QTP_MIN_CASH_RESERVE_FRACTION="${QTP_MIN_CASH_RESERVE_FRACTION:-0.01}"
QTP_MAX_DRAWDOWN_FRACTION="${QTP_MAX_DRAWDOWN_FRACTION:-0.20}"
QTP_MAX_REALIZED_LOSS="${QTP_MAX_REALIZED_LOSS:-25000}"
QTP_MAXIMUM_GAP_DAYS="${QTP_MAXIMUM_GAP_DAYS:-7}"
QTP_EXECUTABLE="${QTP_EXECUTABLE:-/opt/quantum-trader-pro/venv/bin/quantum-trader}"

if [[ ! -r "${QTP_DATA_FILE}" ]]; then
  printf 'Input is not readable: %s\n' "${QTP_DATA_FILE}" >&2
  exit 2
fi
if [[ ! -x "${QTP_EXECUTABLE}" ]]; then
  printf 'Executable is not available: %s\n' "${QTP_EXECUTABLE}" >&2
  exit 2
fi

run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="${QTP_STATE_ROOT}/runs/${run_stamp}"
mkdir -p "${run_dir}"

exec "${QTP_EXECUTABLE}" simulate \
  --mode simulation \
  --data "${QTP_DATA_FILE}" \
  --output "${run_dir}" \
  --symbol "${QTP_SYMBOL}" \
  --initial-cash "${QTP_INITIAL_CASH}" \
  --fast-window "${QTP_FAST_WINDOW}" \
  --slow-window "${QTP_SLOW_WINDOW}" \
  --invested-fraction "${QTP_INVESTED_FRACTION}" \
  --slippage-bps "${QTP_SLIPPAGE_BPS}" \
  --fee-per-order "${QTP_FEE_PER_ORDER}" \
  --fee-per-share "${QTP_FEE_PER_SHARE}" \
  --max-position-fraction "${QTP_MAX_POSITION_FRACTION}" \
  --max-order-notional "${QTP_MAX_ORDER_NOTIONAL}" \
  --min-cash-reserve-fraction "${QTP_MIN_CASH_RESERVE_FRACTION}" \
  --max-drawdown-fraction "${QTP_MAX_DRAWDOWN_FRACTION}" \
  --max-realized-loss "${QTP_MAX_REALIZED_LOSS}" \
  --maximum-gap-days "${QTP_MAXIMUM_GAP_DAYS}"
