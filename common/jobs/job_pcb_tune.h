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

#ifndef JOB_PCB_TUNE_H
#define JOB_PCB_TUNE_H

#include <kicommon.h>
#include <optional>
#include <vector>
#include "job.h"

/**
 * kicad-cli pcb tune (simee): lengthen routed nets with KiCad's own length tuner, headless.
 *
 * Each net gets a tuning pattern on its longest straight segment, tuned to m_length (mm) when
 * given, else to the longest of the nets (a skew group), else to the board's DRC length rule.
 * DIFF_PAIR tunes the net's differential pair as one; SKEW meanders the net alone against its
 * pair's other net, to m_skew or the board's skew rule. Each net is then measured as DRC measures
 * it and retuned by the difference while it is outside the target.
 */
class KICOMMON_API JOB_PCB_TUNE : public JOB
{
public:
    JOB_PCB_TUNE();

    wxString              m_filename;
    wxString              m_output; ///< empty: overwrite m_filename
    std::vector<wxString> m_nets;
    std::optional<double> m_length; ///< mm
    std::optional<double> m_skew;   ///< mm, for SKEW
    enum class MODE { SINGLE, DIFF_PAIR, SKEW } m_mode = MODE::SINGLE;
    enum class REPORT_FORMAT { TEXT, JSON } m_reportFormat = REPORT_FORMAT::TEXT;
    wxString              m_reportFile; ///< empty: print the report
};

#endif
