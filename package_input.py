import json
import os
import re
import tempfile
from re import sub

import boto3
import dateutil.parser
import shortuuid
import unicodecsv as csv
from enum import Enum
from sqlalchemy.sql import text

import package
from app import db, logger
from excel import convert_spreadsheet_to_csv
from purge_cache import purge_the_cache
from util import convert_to_utf_8
from util import safe_commit


class PackageInput:
    @staticmethod
    def normalize_date(date_str, warn_if_blank=False, default=None):
        if date_str:
            try:
                return dateutil.parser.parse(date_str, default=default).isoformat()
            except Exception:
                return ParseWarning.bad_date
        else:
            return ParseWarning.bad_date if warn_if_blank else None

    @staticmethod
    def normalize_year(year, warn_if_blank=False):
        if year:
            try:
                return dateutil.parser.parse(year).year
            except Exception:
                return ParseWarning.bad_year
        else:
            return ParseWarning.bad_year if warn_if_blank else None

    @staticmethod
    def normalize_int(value, warn_if_blank=False):
        if value:
            try:
                return int(value)
            except Exception:
                return ParseWarning.bad_int
        else:
            return ParseWarning.bad_int if warn_if_blank else None

    @staticmethod
    def normalize_price(price, warn_if_blank=False):
        if price:
            try:
                decimal = u',' if re.search(ur'\.\d{3}', price) or re.search(ur',\d{2}$', price) else ur'\.'
                sub_pattern = ur'[^\d{}]'.format(decimal)
                price = sub(sub_pattern, '', price)
                price = sub(',', '.', price)
                return int(round(float(price)))
            except Exception:
                return ParseWarning.bad_usd_price
        else:
            return ParseWarning.bad_usd_price if warn_if_blank else None

    @staticmethod
    def normalize_issn(issn, warn_if_blank=False):
        if issn:
            issn = sub(ur'\s', '', issn).upper()
            if re.match(ur'^\d{4}-\d{3}(?:X|\d)$', issn):
                return issn
            elif re.match(ur'^[A-Z0-9]{4}-\d{3}(?:X|\d)$', issn):
                return ParseWarning.bundle_issn
            else:
                return ParseWarning.bad_issn
        else:
            return ParseWarning.bad_issn if warn_if_blank else None

    @staticmethod
    def strip_text(txt, warn_if_blank=False):
        if txt is not None:
            return txt.strip()
        else:
            return ParseWarning.blank_text if warn_if_blank else None


    @classmethod
    def csv_columns(cls):
        raise NotImplementedError()

    @classmethod
    def import_view_name(cls):
        raise NotImplementedError()

    @classmethod
    def destination_table(cls):
        raise NotImplementedError()

    @classmethod
    def translate_row(cls, row):
        return [row]

    @classmethod
    def ignore_row(cls, row):
        return False

    @classmethod
    def apply_header(cls, normalized_rows, header_rows):
        return normalized_rows

    @classmethod
    def normalize_cell(cls, column_name, column_value):
        for canonical_name, spec in cls.csv_columns().items():
            for snippet in spec['name_snippets']:
                snippet = snippet.lower()
                column_name = column_name.strip().lower()
                exact_name = spec.get('exact_name', False)
                if (exact_name and snippet == column_name) or (not exact_name and snippet in column_name.lower()):
                    return {canonical_name: spec['normalize'](column_value, spec.get('warn_if_blank', False))}

        return None

    @classmethod
    def _copy_to_s3(cls, package_id, filename):
        s3 = boto3.client('s3')
        bucket_name = 'jump-redshift-staging'
        object_name = '{}_{}_{}'.format(package_id, cls.__name__, shortuuid.uuid())
        s3.upload_file(filename, bucket_name, object_name)
        return 's3://{}/{}'.format(bucket_name, object_name)

    @classmethod
    def delete(cls, package_id):
        num_deleted = db.session.query(cls).filter(cls.package_id == package_id).delete()
        db.session.execute("delete from {} where package_id = '{}'".format(cls.destination_table(), package_id))

        my_package = db.session.query(package.Package).filter(package.Package.package_id == package_id).scalar()
        if my_package:
            cls.clear_caches(my_package)

        safe_commit(db)

        return u'Deleted {} {} rows for package {}.'.format(num_deleted, cls.__name__, package_id)

    @classmethod
    def clear_caches(cls, my_package):
        my_package.clear_package_counter_breakdown_cache()
        purge_the_cache(my_package.package_id)

    @classmethod
    def update_dest_table(cls, package_id):
        # unload_cmd = text('''
        #     unload
        #     ('select * from {view} where package_id = \\'{package_id}\\'')
        #     to 's3://jump-redshift-staging/{package_id}_{view}_{uuid}/'
        #     with credentials :creds csv'''.format(
        #         view=cls.import_view_name(),
        #         package_id=package_id,
        #         uuid=shortuuid.uuid(),
        #     )
        # )
        #
        # aws_creds = 'aws_access_key_id={aws_key};aws_secret_access_key={aws_secret}'.format(
        #     aws_key=os.getenv('AWS_ACCESS_KEY_ID'),
        #     aws_secret=os.getenv('AWS_SECRET_ACCESS_KEY')
        # )
        #
        # db.session.execute(unload_cmd.bindparams(creds=aws_creds))

        db.session.execute("delete from {} where package_id = '{}'".format(cls.destination_table(), package_id))

        db.session.execute(
            "insert into {} (select * from {} where package_id = '{}')".format(
                cls.destination_table(), cls.import_view_name(), package_id
            )
        )

    @classmethod
    def normalize_rows(cls, file_name):
        if file_name.endswith(u'.xls') or file_name.endswith(u'.xlsx'):
            csv_file_name = convert_spreadsheet_to_csv(file_name, parsed=False)
            if csv_file_name is None:
                raise RuntimeError(u'{} could not be opened as a spreadsheet'.format(file_name))
            else:
                file_name = csv_file_name

        file_name = convert_to_utf_8(file_name)
        logger.info('converted file: {}'.format(file_name))

        with open(file_name, 'r') as csv_file:
            dialect = csv.Sniffer().sniff(csv_file.readline())
            csv_file.seek(0)

            # find the index of the first complete header row
            max_columns = 0
            header_index = None
            parsed_rows = []
            line_no = 0
            for line in csv.reader(csv_file, dialect=dialect):
                if not any([cell.strip() for cell in line]):
                    continue

                parsed_rows.append(line)

                if len(line) > max_columns and all(line):
                    max_columns = len(line)
                    header_index = line_no
                    logger.info(u'candidate header row: {}'.format(u', '.join(line)))

                line_no += 1

            if header_index is None:
                raise RuntimeError(u"Couldn't identify a header row in the file")

            row_dicts = [dict(zip(parsed_rows[header_index], x)) for x in parsed_rows[header_index+1:]]

            normalized_rows = []
            warnings = []
            for row_no, row in enumerate(row_dicts):
                normalized_row = {}
                row_warnings = []

                for column_name in row.keys():
                    try:
                        normalized_cell = cls.normalize_cell(column_name, row[column_name])
                        if isinstance(normalized_cell, dict):
                            normalized_name, normalized_value = normalized_cell.items()[0]
                            if isinstance(normalized_value, ParseWarning):
                                warning = {'row_number': row_no + 1, 'column': column_name, 'value': row[column_name]}
                                warning.update(normalized_value.value)
                                row_warnings.append(warning)
                                normalized_row.setdefault(normalized_name, None)
                            else:
                                normalized_row.setdefault(normalized_name, normalized_value)
                    except Exception as e:
                        raise RuntimeError(u'Error reading row {}: {} for {}: "{}"'.format(
                            row_no + 1, e.message, column_name, row[column_name]
                        ))

                if cls.ignore_row(normalized_row):
                    continue

                row_keys = sorted(normalized_row.keys())
                expected_keys = sorted([k for k, v in cls.csv_columns().items() if v.get('required', True)])

                if set(expected_keys).difference(set(row_keys)):
                    raise RuntimeError(u'Missing expected columns. Expected {} but got {}.'.format(
                        ', '.join(expected_keys),
                        ', '.join(row_keys)
                    ))

                normalized_rows.extend(cls.translate_row(normalized_row))
                warnings.extend(row_warnings)

            cls.apply_header(normalized_rows, parsed_rows[0:header_index+1])

            return normalized_rows, warnings

    @classmethod
    def load(cls, package_id, file_name, commit=False):
        try:
            normalized_rows, warnings = cls.normalize_rows(file_name)
        except (RuntimeError, UnicodeError) as e:
            return {'success': False, 'message': e.message, 'warnings': []}

        for row in normalized_rows:
            row.update({'package_id': package_id})
            logger.info(u'normalized row: {}'.format(json.dumps(row)))

        db.session.query(cls).filter(cls.package_id == package_id).delete()

        if normalized_rows:
            sorted_fields = sorted(normalized_rows[0].keys())
            normalized_csv_filename = tempfile.mkstemp()[1]
            with open(normalized_csv_filename, 'w') as normalized_csv_file:
                writer = csv.DictWriter(normalized_csv_file, delimiter=',', encoding='utf-8', fieldnames=sorted_fields)
                for row in normalized_rows:
                    writer.writerow(row)

            s3_object = cls._copy_to_s3(package_id, normalized_csv_filename)

            copy_cmd = text('''
                copy {table}({fields}) from '{s3_object}'
                credentials :creds format as csv
                timeformat 'auto';
            '''.format(
                table=cls.__tablename__,
                fields=', '.join(sorted_fields),
                s3_object=s3_object,
            ))

            aws_creds = 'aws_access_key_id={aws_key};aws_secret_access_key={aws_secret}'.format(
                aws_key=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret=os.getenv('AWS_SECRET_ACCESS_KEY')
            )

            db.session.execute(copy_cmd.bindparams(creds=aws_creds))
            cls.update_dest_table(package_id)

            my_package = db.session.query(package.Package).filter(package.Package.package_id == package_id).scalar()

            if commit:
                safe_commit(db)
                if my_package:
                    cls.clear_caches(my_package)

        return {
            'success': True,
            'message': u'Inserted {} {} rows for package {}.'.format(len(normalized_rows), cls.__name__, package_id),
            'warnings': warnings
        }


class ParseWarning(Enum):
    bad_issn = {
        'label': 'bad_issn',
        'text': 'Invalid ISSN format.'
    }
    bundle_issn = {
        'label': 'bundle_issn',
        'text': 'ISSN represents a bundle of journals, not a single journal.'
    }
    bad_date = {
        'label': 'bad_date',
        'text': 'Unrecognized date format.'
    }
    bad_year = {
        'label': 'bad_year',
        'text': 'Unrecognized date or year.'
    }
    bad_int = {
        'label': 'bad_int',
        'text': 'Unrecognized integer format.'
    }
    bad_usd_price = {
        'label': 'bad_usd_price',
        'text': 'Unrecognized USD format.'
    }
    blank_text = {
        'label': 'blank_text',
        'text': 'Expected text here.'
    }
