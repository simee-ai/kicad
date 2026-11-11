/*
 * This program source code file is part of KiCad, a free EDA CAD application.
 *
 * Copyright The KiCad Developers, see AUTHORS.txt for contributors.
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation, either version 3 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef PCB_TUNE_JOB_H
#define PCB_TUNE_JOB_H

class BOARD;
class JOB_PCB_TUNE;
class PROGRESS_REPORTER;
class REPORTER;
class TOOL_MANAGER;

/**
 * Run `kicad-cli pcb tune` (JOB_PCB_TUNE) on a loaded board: tune each net, refill the zones,
 * save the board and write the report.
 *
 * @return a CLI::EXIT_CODES value: SUCCESS when every net is tuned, ERR_RC_VIOLATIONS when one
 *         is not, ERR_ARGS for a net the board lacks.
 */
int RunPcbTune( JOB_PCB_TUNE& aJob, BOARD* aBoard, TOOL_MANAGER* aToolManager, REPORTER& aReporter,
                PROGRESS_REPORTER* aProgressReporter );

#endif
