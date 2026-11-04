Each row in samples.csv represents one independent mouse, as confirmed by the
researcher. measurement_id is also the measurement filename identifier;
original_sample_id preserves the supplied Lux identifier.

filename_date is extracted from the filename. Its meaning is unconfirmed and it
must not be interpreted as an acquisition date until verified. The supplied
record date 22.09.2006 is preserved verbatim in supplied_record_date; its meaning
and year need confirmation.

Blank exact_age, age_unit, sex, preparation_batch, acquisition_batch and
section_id fields mean unknown, not an assumed shared value. The numeric suffix
after the measurement ID in filenames is preserved without interpretation.

Do not add personal or restricted laboratory information to GitHub without
checking the lab's sharing rules.
