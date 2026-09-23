#pragma once

// Boost 1.88 retains the complete Process v1 implementation but no longer
// ships its historical umbrella header.  The pinned bpftime exact-load patch
// deliberately selects v1, so provide that umbrella without changing bpftime
// or the system Boost installation.
#ifndef BOOST_PROCESS_VERSION
#define BOOST_PROCESS_VERSION 1
#endif

#include <boost/process/v1/args.hpp>
#include <boost/process/v1/child.hpp>
#include <boost/process/v1/env.hpp>
#include <boost/process/v1/environment.hpp>
#include <boost/process/v1/io.hpp>
#include <boost/process/v1/pipe.hpp>
#include <boost/process/v1/start_dir.hpp>
