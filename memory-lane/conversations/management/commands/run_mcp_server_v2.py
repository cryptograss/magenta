"""
Django management command to run the MCP server.

Simple wrapper that sets up logging and delegates to the MCP server module.
"""

from django.core.management.base import BaseCommand
from django.conf import settings
import asyncio
import logging
import sys
from pathlib import Path

from conversations.mcp.server import run_mcp_server


class Command(BaseCommand):
    help = 'Run MCP server for conversation memory access (refactored version)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--port',
            type=int,
            default=8000,
            help='Port to run the MCP server on (default: 8000)'
        )

    def handle(self, *args, **options):
        """Set up logging and run the MCP server"""

        # Set up file logging - use BASE_DIR from settings
        log_dir = Path(settings.BASE_DIR) / 'logs'
        log_dir.mkdir(exist_ok=True, parents=True)
        log_file = log_dir / 'mcp_server_v2.log'

        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stderr)
            ]
        )

        logger = logging.getLogger(__name__)
        logger.info(f"MCP Server v2 logging to {log_file}")

        # Run the server
        port = options['port']
        logger.info(f"Starting MCP server on port {port}")

        asyncio.run(run_mcp_server(port=port))
