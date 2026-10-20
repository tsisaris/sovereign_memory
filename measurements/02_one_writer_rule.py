"""Measurement 02."""

import datetime
import multiprocessing
import time

import duckdb


def writer(filename, ready_event):
    """Writer."""
    try:
        conn = duckdb.connect(filename, read_only=False)
        ready_event.set()
        time.sleep(2)
        conn.close()
    except duckdb.Error as e:
        print(f"Writer exception: {e}")


def reader(filename, output_dict):
    """Reader."""
    try:
        conn = duckdb.connect(filename, read_only=True)
        output_dict["success"] = True
        conn.close()
    except duckdb.Error as e:
        output_dict["success"] = False
        output_dict["error"] = str(e)


def main():
    """Main."""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    out = []
    out.append(f"Date: {date_str}")
    out.append("Measurement: One-writer rule (read-write and read-only concurrently)")

    filename = "test_one_writer.duckdb"
    manager = multiprocessing.Manager()
    ready_event = manager.Event()
    output_dict = manager.dict()

    p_writer = multiprocessing.Process(target=writer, args=(filename, ready_event))
    p_writer.start()

    ready_event.wait()

    p_reader = multiprocessing.Process(target=reader, args=(filename, output_dict))
    p_reader.start()
    p_reader.join()
    p_writer.join()

    if output_dict.get("success"):
        out.append("Result: A second process CAN open the same file read-only.")
    else:
        out.append("Result: A second process CANNOT open the same file read-only.")
        out.append(f"DuckDB's exact message: {output_dict.get('error')}")

    with open("measurements/02_one_writer_rule.md", "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
