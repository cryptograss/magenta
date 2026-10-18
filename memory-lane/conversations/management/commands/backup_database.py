"""
Django management command to create PostgreSQL database backups.

Usage:
    python manage.py backup_database                    # Create compressed backup with blockheight
    python manage.py backup_database --no-compress      # Create uncompressed SQL file
    python manage.py backup_database --output custom.sql.gz  # Specify output filename
"""

from django.core.management.base import BaseCommand
from django.conf import settings
import subprocess
import os
import json
from pathlib import Path
from datetime import datetime


class Command(BaseCommand):
    help = 'Create a PostgreSQL database backup'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            type=str,
            help='Output filename (default: BLOCKHEIGHT_magent_YYYYMMDD_HHMMSS.sql.gz)',
        )
        parser.add_argument(
            '--no-compress',
            action='store_true',
            help='Do not compress the backup (saves as .sql instead of .sql.gz)',
        )
        parser.add_argument(
            '--directory',
            type=str,
            default='backups',
            help='Directory to save backup (default: backups/)',
        )

    def get_current_blockheight(self):
        """Get current Ethereum block height for filename."""
        try:
            # Navigate from magenta/ to arthel/src/build_logic/
            script_path = Path(__file__).parent.parent.parent.parent.parent / 'src' / 'build_logic' / 'get_current_blockheight.js'

            # Pass environment variables including ALCHEMY_API_KEY
            env = os.environ.copy()

            # Change to arthel directory so dotenv finds .env file
            arthel_dir = Path(__file__).parent.parent.parent.parent.parent

            result = subprocess.run(
                ['node', str(script_path)],
                capture_output=True,
                text=True,
                env=env,
                cwd=str(arthel_dir),
            )
            if result.returncode == 0:
                # Parse the output to extract just the block number
                for line in result.stdout.split('\n'):
                    if line.startswith('Current Ethereum Block Height:'):
                        block_str = line.split(':')[1].strip()
                        return block_str
                return 'unknown'
            else:
                return 'unknown'
        except Exception:
            return 'unknown'

    def handle(self, *args, **options):
        # Get database settings
        db_settings = settings.DATABASES['default']
        db_name = db_settings['NAME']
        db_user = db_settings['USER']
        db_host = db_settings['HOST']
        db_port = db_settings['PORT']
        db_password = db_settings.get('PASSWORD', '')

        # Determine output filename
        if options['output']:
            filename = options['output']
        else:
            blockheight = self.get_current_blockheight()
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            extension = '.sql' if options['no_compress'] else '.sql.gz'
            filename = f'{blockheight}_magent_{timestamp}{extension}'

        # Create backup directory if it doesn't exist
        backup_dir = Path(options['directory'])
        backup_dir.mkdir(exist_ok=True)

        output_path = backup_dir / filename

        self.stdout.write(self.style.WARNING(f'Creating backup of {db_name}...'))
        self.stdout.write(f'Output: {output_path}')

        # Build pg_dump command
        env = os.environ.copy()
        if db_password:
            env['PGPASSWORD'] = db_password

        pg_dump_cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '--verbose',
        ]

        try:
            if options['no_compress']:
                # Save directly to SQL file
                with open(output_path, 'w') as f:
                    result = subprocess.run(
                        pg_dump_cmd,
                        env=env,
                        stdout=f,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
            else:
                # Pipe through gzip
                pg_dump_process = subprocess.Popen(
                    pg_dump_cmd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                with open(output_path, 'wb') as f:
                    gzip_process = subprocess.Popen(
                        ['gzip'],
                        stdin=pg_dump_process.stdout,
                        stdout=f,
                        stderr=subprocess.PIPE,
                    )

                    pg_dump_process.stdout.close()
                    _, gzip_stderr = gzip_process.communicate()
                    _, pg_dump_stderr = pg_dump_process.wait(), pg_dump_process.stderr.read()

                    result = gzip_process

            if result.returncode == 0:
                # Get file size
                size_bytes = output_path.stat().st_size
                size_mb = size_bytes / (1024 * 1024)

                self.stdout.write(
                    self.style.SUCCESS(
                        f'\n✓ Backup created successfully: {output_path} ({size_mb:.2f} MB)'
                    )
                )

                # Show how to restore
                self.stdout.write('\nTo restore this backup:')
                if options['no_compress']:
                    self.stdout.write(
                        f'  psql -h {db_host} -U {db_user} -d {db_name} < {output_path}'
                    )
                else:
                    self.stdout.write(
                        f'  gunzip -c {output_path} | psql -h {db_host} -U {db_user} -d {db_name}'
                    )

                return str(output_path)
            else:
                self.stdout.write(
                    self.style.ERROR(f'Backup failed with return code {result.returncode}')
                )
                if hasattr(result, 'stderr') and result.stderr:
                    self.stdout.write(self.style.ERROR(result.stderr))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error creating backup: {str(e)}'))
            raise
