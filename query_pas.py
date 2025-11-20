"""Helper CLI for listing unique values and filtered markdown tables from PASReport.csv."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List, NamedTuple, Sequence

import pandas as pd

DEFAULT_CSV = (
    Path(__file__).parent
    / "Test Programs"
    / "PTUSDJXA1H21G402546"
    / "Reports"
    / "PASReport.csv"
)


def list_unique_values(
    frame: pd.DataFrame,
    columns: Iterable[str],
    *,
    joiner: str = "_",
    include_empty: bool = False,
) -> list[str]:
    """Return unique values for the given columns, joining with *joiner* when needed."""

    column_list = [col.strip() for col in columns if col.strip()]
    if not column_list:
        raise ValueError("At least one column must be specified.")

    missing = [col for col in column_list if col not in frame.columns]
    if missing:
        raise ValueError(
            "Column(s) {missing} not found. Available columns: {available}".format(
                missing=", ".join(missing), available=", ".join(frame.columns)
            )
        )

    working = frame[column_list].fillna("").astype(str)
    combined = (
        working.agg(joiner.join, axis=1) if len(column_list) > 1 else working.iloc[:, 0]
    )

    if not include_empty:
        combined = combined[combined.str.strip() != ""]

    unique_values = combined.drop_duplicates().tolist()
    return unique_values


class FilterClause(NamedTuple):
    column: str
    value: str
    mode: str  # "equals" or "contains"


def parse_filters(filter_args: Sequence[str]) -> List[FilterClause]:
    """Turn CLI filter expressions into filter clauses.

    Supported syntaxes:
    * Column=Value      -> exact match (case-sensitive)
    * Column~=Value     -> substring match (case-insensitive)
    """

    parsed: List[FilterClause] = []
    for expr in filter_args:
        if "~=" in expr:
            column, value = expr.split("~=", 1)
            mode = "contains"
        elif "=" in expr:
            column, value = expr.split("=", 1)
            mode = "equals"
        else:
            raise ValueError(
                f"Invalid filter '{expr}'. Use Column=Value or Column~=Value syntax."
            )

        column = column.strip()
        value = value.strip()
        if not column:
            raise ValueError(f"Invalid filter '{expr}': column name is empty.")
        parsed.append(FilterClause(column=column, value=value, mode=mode))
    return parsed


def apply_filters(frame: pd.DataFrame, filters: Sequence[FilterClause]) -> pd.DataFrame:
    """Return a filtered dataframe according to the provided column/value pairs."""

    if not filters:
        return frame

    working = frame
    for clause in filters:
        column = clause.column
        value = clause.value
        if column not in working.columns:
            raise ValueError(
                "Filter column '{column}' not found. Available columns: {available}".format(
                    column=column, available=", ".join(working.columns)
                )
            )
        series = working[column].fillna("").astype(str)
        if clause.mode == "contains":
            mask = series.str.contains(value, case=False, na=False)
        else:
            mask = series == value
        working = working[mask]
    return working


def dataframe_to_markdown(
    frame: pd.DataFrame,
    columns: Sequence[str],
    *,
    limit: int | None = None,
) -> str:
    """Return a Markdown table containing the requested *columns* from *frame*."""

    if not columns:
        raise ValueError("At least one column must be supplied for the markdown table.")

    missing = [col for col in columns if col not in frame.columns]
    if missing:
        raise ValueError(
            "Markdown column(s) {missing} not found. Available columns: {available}".format(
                missing=", ".join(missing), available=", ".join(frame.columns)
            )
        )

    subset = frame[columns]
    if limit is not None:
        subset = subset.head(limit)

    if subset.empty:
        return "No rows match the supplied filters, so no table output."

    subset = subset.fillna("").astype(str)
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in subset.values.tolist()]
    return "\n".join([header, divider, *rows])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Print the unique values in one or more columns of a CSV file. "
            "When multiple columns are provided, their per-row values are joined "
            "with an underscore to form the unique combinations."
        )
    )
    parser.add_argument(
        "columns",
        nargs="+",
        help="One or more column names (e.g. SubFlow Partition).",
    )
    parser.add_argument(
        "--file",
        "-f",
        default=str(DEFAULT_CSV),
        help="Path to the CSV file. Defaults to PASReport.csv in this repo.",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Include empty/blank rows in the result list.",
    )
    parser.add_argument(
        "--joiner",
        default="_",
        help="String used when joining multiple column values (default: _).",
    )
    parser.add_argument(
        "--sort",
        action="store_true",
        help="Sort alphabetically instead of preserving file order.",
    )
    parser.add_argument(
        "--filter",
        "-F",
        action="append",
        default=[],
        help=(
            "Repeatable filters applied before computing uniques/table. Use Column=Value "
            "for exact matches or Column~=Value for substring contains matches."
        ),
    )
    parser.add_argument(
        "--table",
        action="store_true",
        help="Emit a Markdown table for the filtered rows (defaults to all columns).",
    )
    parser.add_argument(
        "--table-columns",
        "-t",
        nargs="+",
        default=None,
        help="Override the columns included in the Markdown table.",
    )
    parser.add_argument(
        "--table-limit",
        type=int,
        default=25,
        help=(
            "Maximum rows to include in the Markdown table; set to 0 for no limit "
            "(default: 25)."
        ),
    )
    args = parser.parse_args()

    csv_path = Path(args.file).expanduser()
    try:
        filters = parse_filters(args.filter)
        dataframe = pd.read_csv(csv_path, encoding="utf-8-sig")
        filtered = apply_filters(dataframe, filters)
        values = list_unique_values(
            filtered,
            args.columns,
            joiner=args.joiner,
            include_empty=args.include_empty,
        )
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}")
        return

    if args.sort:
        values = sorted(values, key=str.casefold)

    if not values:
        print(
            "No values found using column(s) {columns} in {file}.".format(
                columns=", ".join(args.columns), file=csv_path
            )
        )
        return

    header = (
        "Unique combinations in column '{col}'".format(col=args.columns[0])
        if len(args.columns) == 1
        else "Unique combinations for columns: {cols}".format(
            cols=", ".join(args.columns)
        )
    )
    print(f"{header} ({len(values)} entries):")
    for idx, val in enumerate(values, start=1):
        print(f"{idx:4}: {val}")

    if args.table or args.table_columns:
        try:
            table_columns = args.table_columns or list(filtered.columns)
            table_limit = None if args.table_limit <= 0 else args.table_limit
            markdown = dataframe_to_markdown(
                filtered,
                table_columns,
                limit=table_limit,
            )
        except ValueError as exc:
            print(f"Error while building markdown table: {exc}")
            return

        print("\nMarkdown table of filtered rows:")
        print(markdown)


if __name__ == "__main__":
    main()