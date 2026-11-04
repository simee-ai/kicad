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

#include "command_pcb_tune.h"
#include "jobs/job_pcb_tune.h"
#include "cli/exit_codes.h"
#include <string_utils.h>
#include <wx/crt.h>

#define ARG_NET "--net"
#define ARG_LENGTH "--length"
#define ARG_SKEW "--skew"
#define ARG_MODE "--mode"

CLI::PCB_TUNE_COMMAND::PCB_TUNE_COMMAND() :
        COMMAND( "tune-test" )
{
    addCommonArgs( true, true, IO_TYPE::FILE, IO_TYPE::FILE );
    m_argParser.add_description( UTF8STDSTR( _( "Lengthen routed nets with the length tuner "
                                                "(simee spike)" ) ) );
    m_argParser.set_suppress( true );

    m_argParser.add_argument( ARG_NET )
            .append()
            .required()
            .help( UTF8STDSTR( _( "Net to tune; repeat it for a skew group, tuned to its longest "
                                  "net" ) ) )
            .metavar( "NET" );

    m_argParser.add_argument( ARG_LENGTH )
            .scan<'g', double>()
            .help( UTF8STDSTR( _( "Target length in mm (default: the longest net, or the board's "
                                  "length rule)" ) ) )
            .metavar( "MM" );

    m_argParser.add_argument( ARG_MODE )
            .default_value( std::string( "single" ) )
            .help( UTF8STDSTR( _( "single, diff-pair (the net's pair tuned as one) or skew (the net "
                                  "against its pair's other net)" ) ) )
            .metavar( "MODE" );

    m_argParser.add_argument( ARG_SKEW )
            .scan<'g', double>()
            .help( UTF8STDSTR( _( "Target skew in mm for --mode skew (default: the board's skew "
                                  "rule)" ) ) )
            .metavar( "MM" );
}


int CLI::PCB_TUNE_COMMAND::doPerform( KIWAY& aKiway )
{
    std::unique_ptr<JOB_PCB_TUNE> job = std::make_unique<JOB_PCB_TUNE>();

    job->m_filename = m_argInput;
    job->m_output = m_argOutput;

    for( const std::string& net : m_argParser.get<std::vector<std::string>>( ARG_NET ) )
        job->m_nets.push_back( From_UTF8( net.c_str() ) );

    if( m_argParser.is_used( ARG_LENGTH ) )
        job->m_length = m_argParser.get<double>( ARG_LENGTH );

    if( m_argParser.is_used( ARG_SKEW ) )
        job->m_skew = m_argParser.get<double>( ARG_SKEW );

    std::string mode = m_argParser.get<std::string>( ARG_MODE );

    if( mode == "single" )
        job->m_mode = JOB_PCB_TUNE::MODE::SINGLE;
    else if( mode == "diff-pair" )
        job->m_mode = JOB_PCB_TUNE::MODE::DIFF_PAIR;
    else if( mode == "skew" )
        job->m_mode = JOB_PCB_TUNE::MODE::SKEW;
    else
    {
        wxFprintf( stderr, _( "Unknown mode '%s'\n" ), From_UTF8( mode.c_str() ) );
        return EXIT_CODES::ERR_ARGS;
    }

    if( !wxFile::Exists( job->m_filename ) )
    {
        wxFprintf( stderr, _( "Board file does not exist or is not accessible\n" ) );
        return EXIT_CODES::ERR_INVALID_INPUT_FILE;
    }

    return aKiway.ProcessJob( KIWAY::FACE_PCB, job.get() );
}
